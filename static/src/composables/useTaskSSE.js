import { onMounted, onBeforeUnmount } from 'vue'
import { useTaskProgressStore } from '@/stores/taskProgress'
import { useToast } from '@/composables/useToast'
import { stageText } from '@/utils/taskStages'

// 模块级单例：跨组件挂载仅维持一条 SSE 连接与重连状态
let es = null
let started = false
let reconnectTimer = null
let stopped = false
const knownIds = new Set()
const MAX_RECONNECT = 5
let reconnectCount = 0

/**
 * 统一任务实时流（SSE）消费 hook。
 *
 * 整体思路：
 *   通过 EventSource 连接后端 /tasks/stream（?token= 鉴权），接收任务进度/状态推送，
 *   实时写入 taskProgress store 供悬浮窗与长任务中心展示；任务终态时弹窗通知。
 *   替代原先对 /tasks/running 的 5s 定时轮询，消除无效请求与等待焦虑。
 *
 * 关键点：
 *   1. 原生 EventSource 不能自定义请求头，token 走查询参数（后端已支持）。
 *   2. onerror 自动重连（浏览器原生重连 + 兜底），连接断开期间进度靠后端广播补推。
 *   3. 终态检测：收到 status 非 running 的任务即视为完成，弹对应 toast 后移出已知集合。
 */
export function useTaskSSE() {
  const store = useTaskProgressStore()
  const { notify } = useToast()

  function handleEvent(raw) {
    let payload
    try {
      payload = JSON.parse(raw.data)
    } catch {
      return
    }
    // 全量快照：连接建立时一次性推送。按 id 降序（最近的在前）插入，
    // 保证右下角悬浮窗倒序展示；逐条 unshift 会因后端返回顺序而错位，故先排序。
    if (payload.type === 'snapshot') {
      const snap = [...(payload.tasks || [])].sort((a, b) => (b.id || 0) - (a.id || 0))
      for (const t of snap) {
        upsertTask(t)
        knownIds.add(t.id)
      }
      return
    }
    // 单任务变更
    if (payload.type === 'task') {
      const t = payload.task
      if (!t) return
      upsertTask(t)
      // 终态通知（仅首次收到终态时提示）
      if (t.status !== 'running' && knownIds.has(t.id)) {
        knownIds.delete(t.id)
        if (t.status === 'success') notify(`任务「${t.name}」已完成`, 'success')
        else if (t.status === 'failed') notify(`任务「${t.name}」失败：${t.error || ''}`, 'error')
        else if (t.status === 'cancelled') notify(`任务「${t.name}」已取消`, 'info')
      } else if (t.status === 'running') {
        knownIds.add(t.id)
      }
    }
  }

  function upsertTask(t) {
    store.upsert({
      id: t.id, type: t.type, name: t.name,
      progress: t.progress, stage: stageText(t.stage), status: t.status,
      tokens_in: t.tokens_in, tokens_out: t.tokens_out, error: t.error,
      is_long_task: t.is_long_task, estimated_minutes: t.estimated_duration_minutes,
      estimated_complete_at: t.estimated_complete_at,
    })
  }

  function connect() {
    if (es || stopped) return
    const token = localStorage.getItem('token')
    if (!token) return
    const url = `/api/v1/tasks/stream?token=${encodeURIComponent(token)}`
    es = new EventSource(url)
    es.onmessage = handleEvent
    es.onerror = () => {
      // 连接异常：关闭当前实例，按有限退避重连（最多 MAX_RECONNECT 次，避免无限重连卡死页面）
      if (es) { es.close(); es = null }
      if (stopped || reconnectCount >= MAX_RECONNECT) return
      reconnectCount += 1
      const delay = Math.min(1000 * reconnectCount, 5000)
      reconnectTimer = setTimeout(() => { if (!stopped) connect() }, delay)
    }
    es.onopen = () => { reconnectCount = 0 }
  }

  function start() {
    if (started) return
    started = true
    stopped = false
    reconnectCount = 0
    connect()
  }

  function stop() {
    stopped = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (es) { es.close(); es = null }
    started = false
  }

  onMounted(start)
  onBeforeUnmount(stop)
  return { start, stop, connect }
}
