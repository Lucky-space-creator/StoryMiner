import { onMounted, onBeforeUnmount } from 'vue'
import { getRunningTasks, getTask } from '@/api/tasks'
import { useTaskProgressStore } from '@/stores/taskProgress'
import { useToast } from '@/composables/useToast'
import { stageText } from '@/utils/taskStages'

// 模块级单例：跨组件挂载保持轮询状态与已追踪任务集合（避免重复启动定时器）
let timer = null
let started = false
const knownIds = new Set()
let hideTimer = null

/**
 * 全局任务轮询：每 intervalMs 拉取进行中任务，写入 taskProgress store 供悬浮窗与仪表盘展示；
 * 检测任务完成（离开进行中列表）后拉详情并弹窗通知。在 AppLayout 挂载一次即可全局生效。
 */
export function useTaskPoller(intervalMs = 5000) {
  const store = useTaskProgressStore()
  const { notify } = useToast()

  async function poll() {
    try {
      const res = await getRunningTasks()
      const running = res.data || []
      const runningIds = new Set(running.map((t) => t.id))
      for (const t of running) {
        store.upsert({ id: t.id, type: t.type, name: t.name, progress: t.progress, stage: stageText(t.stage), status: t.status })
        knownIds.add(t.id)
      }
      // 完成检测：曾被追踪但已不在进行中列表的任务
      for (const id of [...knownIds]) {
        if (!runningIds.has(id)) {
          knownIds.delete(id)
          try {
            const d = (await getTask(id)).data
            store.upsert({ id, type: d.type, name: d.name, progress: d.progress, stage: stageText(d.stage), status: d.status, error: d.error })
            if (d.status === 'success') notify(`任务「${d.name}」已完成`, 'success')
            else if (d.status === 'failed') notify(`任务「${d.name}」失败：${d.error || ''}`, 'error')
          } catch {
            /* 详情拉取失败忽略 */
          }
        }
      }
      // 无进行中任务时延迟收起悬浮窗
      if (running.length === 0) {
        if (hideTimer) clearTimeout(hideTimer)
        hideTimer = setTimeout(() => {
          if (store.tasks.every((t) => t.status !== 'running')) store.hide()
        }, 3000)
      } else if (hideTimer) {
        clearTimeout(hideTimer)
        hideTimer = null
      }
    } catch {
      /* 轮询异常不阻断主流程 */
    }
  }

  function start() {
    if (started) return
    started = true
    poll()
    timer = setInterval(poll, intervalMs)
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    started = false
  }

  onMounted(start)
  onBeforeUnmount(stop)
  return { poll, start, stop }
}
