<template>
  <div class="space-y-6">
    <!-- 页面标题与说明 -->
    <div>
      <h2 class="text-lg font-semibold text-app">长任务中心</h2>
      <p class="text-sm text-muted mt-1">
        以下为预估耗时超过 10 分钟的长任务，提交后即可离开，完成后状态自动更新。
        短任务请前往「仪表盘」查看进度条。
      </p>
    </div>

    <!-- 状态汇总卡片 -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
      <Card v-for="s in summaryCards" :key="s.key" class="cursor-pointer" :class="{ 'ring-2 ring-teal-500': filters.status === s.key }" @click="onStatusTab(s.key)">
        <p class="text-xs text-muted">{{ s.label }}</p>
        <p class="text-xl font-semibold mt-1" :class="s.color">{{ s.value }}</p>
      </Card>
    </div>

    <!-- 任务类型筛选 -->
    <div class="flex items-center gap-2 text-sm">
      <span class="text-muted">任务类型：</span>
      <button
        v-for="opt in typeOptions" :key="opt.key"
        @click="filters.type = opt.key; onSearch()"
        :class="[
          'px-3 py-1 rounded-full border text-xs transition',
          filters.type === opt.key
            ? 'bg-accent text-white border-accent'
            : 'bg-surface text-app border-stone-200 hover:border-teal-400'
        ]"
      >{{ opt.label }}</button>
    </div>

    <!-- 任务列表 -->
    <div class="space-y-2">
      <div
        v-for="t in tasks" :key="t.id"
        class="border border-stone-200 rounded-lg p-4 cursor-pointer hover:border-teal-400 transition"
        @click="openDetail(t)"
      >
        <div class="flex items-center justify-between gap-3">
          <!-- 左侧：任务信息 -->
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 mb-1">
              <span class="font-medium text-app truncate">{{ t.name || ('任务 #' + t.id) }}</span>
              <span class="text-xs px-1.5 py-0.5 rounded bg-surface2 text-accent shrink-0">{{ typeText(t.type) }}</span>
            </div>
            <div class="flex items-center gap-2 text-xs text-muted">
              <span>阶段：{{ stageLabel(t.stage) }}</span>
              <span>·</span>
              <span>创建于 {{ fmt(t.created_at) }}</span>
            </div>
          </div>

          <!-- 右侧：状态 + 预计完成 -->
          <div class="shrink-0 text-right">
            <p :class="statusClass(t.status)" class="text-sm font-medium">{{ statusLabel(t.status) }}</p>
            <!-- 长任务不展示进度条，改展示预计完成时刻 -->
            <p v-if="t.is_long_task && t.estimated_complete_at" class="text-xs text-muted mt-0.5">
              预计 {{ fmtETC(t.estimated_complete_at) }}
            </p>
            <p v-else-if="t.estimated_duration_minutes" class="text-xs text-muted mt-0.5">
              预估 {{ t.estimated_duration_minutes }} 分钟
            </p>
            <p v-if="t.tokens_in || t.tokens_out" class="text-xs text-muted mt-0.5">
              Token：{{ t.tokens_in }} 入 / {{ t.tokens_out }} 出
            </p>
            <Button
              v-if="t.status === 'running'"
              size="sm" variant="ghost" :disabled="cancelling[t.id]"
              @click.stop="onCancel(t)" class="mt-1"
            >
              {{ cancelling[t.id] ? '取消中…' : '取消任务' }}
            </Button>
          </div>
        </div>
        <!-- 错误信息 -->
        <p v-if="t.error" class="mt-2 text-xs text-red-600 truncate" :title="t.error">{{ t.error }}</p>
      </div>

      <p v-if="!tasks.length" class="text-sm text-muted text-center py-10">
        暂无长任务。超长耗时任务会自动出现在这里，提交后即可放心离开。
      </p>
    </div>

    <!-- 分页 -->
    <div v-if="total > 0" class="flex items-center justify-between text-sm">
      <span class="text-muted">共 {{ total }} 条 · 第 {{ page }}/{{ totalPages }} 页</span>
      <div class="flex gap-2">
        <Button size="sm" variant="ghost" :disabled="page <= 1" @click="prevPage">上一页</Button>
        <Button size="sm" variant="ghost" :disabled="page >= totalPages" @click="nextPage">下一页</Button>
      </div>
    </div>

    <!-- 任务详情抽屉 -->
    <Drawer v-model="drawerVisible" :title="`长任务 #${selectedTask?.id ?? ''} 详情`">
      <div v-if="selectedTask" class="space-y-4 text-sm">
        <div class="grid grid-cols-3 gap-2">
          <div><p class="text-muted">状态</p><p :class="statusClass(selectedTask.status)">{{ statusLabel(selectedTask.status) }}</p></div>
          <div><p class="text-muted">阶段</p><p class="text-app">{{ stageLabel(selectedTask.stage) }}</p></div>
          <div><p class="text-muted">任务类型</p><p class="text-app">{{ typeText(selectedTask.type) }}</p></div>
          <div>
            <p class="text-muted">预估耗时</p>
            <p class="text-app">
              {{ selectedTask.estimated_duration_minutes ? selectedTask.estimated_duration_minutes + ' 分钟' : '—' }}
            </p>
          </div>
          <div>
            <p class="text-muted">预计完成</p>
            <p class="text-app">{{ selectedTask.estimated_complete_at ? fmt(selectedTask.estimated_complete_at) : '—' }}</p>
          </div>
          <div><p class="text-muted">Token 消耗</p><p class="text-app">{{ (selectedTask.tokens_in || 0) }} 入 / {{ (selectedTask.tokens_out || 0) }} 出</p></div>
        </div>
        <div class="space-y-1">
          <p class="text-muted">小说 ID：<span class="text-app">{{ selectedTask.novel_id ?? '—' }}</span></p>
          <p class="text-muted">创建：<span class="text-app">{{ selectedTask.created_at ? fmt(selectedTask.created_at) : '—' }}</span></p>
          <p class="text-muted">开始：<span class="text-app">{{ selectedTask.started_at ? fmt(selectedTask.started_at) : '—' }}</span></p>
          <p class="text-muted">结束：<span class="text-app">{{ selectedTask.finished_at ? fmt(selectedTask.finished_at) : '—' }}</span></p>
        </div>
        <div>
          <p class="text-muted mb-1">错误原因</p>
          <pre v-if="selectedTask.error" class="whitespace-pre-wrap break-words bg-stone-50 border border-stone-200 rounded p-2 text-red-600 text-xs">{{ selectedTask.error }}</pre>
          <p v-else class="text-app text-xs">无</p>
        </div>
        <Button v-if="selectedTask.status === 'running'" variant="ghost" :disabled="cancelling[selectedTask.id]" @click="onCancel(selectedTask)">
          {{ cancelling[selectedTask.id] ? '取消中…' : '取消任务' }}
        </Button>
      </div>
    </Drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import Card from '@/components/ui/Card.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Button from '@/components/ui/Button.vue'
import { cancelTask } from '@/api/tasks'
import { typeText, stageText, statusText } from '@/utils/taskStages'
import { useTaskProgressStore } from '@/stores/taskProgress'
import http from '@/api/http'

// ---------- 状态常量 ----------
function stageLabel(s) { return stageText(s) || s || '未知' }
function statusLabel(s) { return statusText(s) || s || '未知' }
function statusClass(s) {
  if (s === 'success') return 'text-emerald-600'
  if (s === 'failed') return 'text-red-600'
  if (s === 'cancelled') return 'text-stone-500'
  return 'text-amber-500'
}
function fmt(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** ETC 格式化：已过期的显示"已超时"，未来的显示时刻 */
function fmtETC(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (d <= new Date()) return '已超时'
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `预计 ${h}:${m} 完成`
}

// ---------- 数据状态 ----------
const tasks = ref([])
const summaryCards = ref([
  { key: '', label: '全部', value: 0, color: 'text-app' },
  { key: 'running', label: '进行中', value: 0, color: 'text-amber-500' },
  { key: 'success', label: '已完成', value: 0, color: 'text-emerald-600' },
  { key: 'failed', label: '失败', value: 0, color: 'text-red-600' },
  { key: 'cancelled', label: '已取消', value: 0, color: 'text-stone-500' },
])
const typeOptions = [
  { key: '', label: '全部类型' },
  { key: 'character_analysis', label: '人物分析' },
  { key: 'chapter_analysis', label: '章节解析' },
  { key: 'parse', label: '文档解析' },
  { key: 'graph', label: '图谱抽取' },
]

const filters = ref({ status: '', type: '' })
const page = ref(1)
const pageSize = ref(15)
const total = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const cancelling = ref({})

// SSE 实时流：长任务中心订阅全局 taskProgress store，进行中任务由 SSE 实时推送，
// 历史/分页任务仍由 /tasks/long 初始加载与筛选查询提供。
const taskStore = useTaskProgressStore()

// ---------- API ----------
async function fetchLongTasks() {
  const params = { page: page.value, page_size: pageSize.value }
  if (filters.value.status) params.status = filters.value.status
  if (filters.value.type) params.type = filters.value.type
  const res = await http.get('/tasks/long', { params })
  tasks.value = res.data.items || []
  total.value = res.data.total || 0
  page.value = res.data.page || 1
  // 本地计算各状态计数（服务端未在 /tasks/long 返回 summary，本地聚合）
  updateSummary()
  // SSE 实时补全：把 store 中该用户进行中的长任务合并进列表（避免等待下次查询）
  mergeLiveTasks()
}

// 将 SSE store 里 is_long_task 且仍 running 的任务合并到当前列表，实现实时进度刷新
function mergeLiveTasks() {
  const live = taskStore.tasks.filter((t) => t.is_long_task && t.status === 'running')
  if (!live.length) return
  const byId = new Map(tasks.value.map((t) => [t.id, t]))
  for (const t of live) {
    if (byId.has(t.id)) {
      // 实时更新进度/阶段，保留分页来源字段
      Object.assign(byId.get(t.id), {
        progress: t.progress, stage: t.stage, status: t.status,
        estimated_complete_at: t.estimated_complete_at, tokens_in: t.tokens_in, tokens_out: t.tokens_out,
      })
    }
  }
  tasks.value = [...tasks.value]
}

function updateSummary() {
  // 因为 /tasks/long 已限定 is_long_task=true，summary 直接本地聚合
  const counts = { '': tasks.value.length, running: 0, success: 0, failed: 0, cancelled: 0 }
  tasks.value.forEach(t => {
    if (typeof counts[t.status] === 'number') counts[t.status]++
  })
  summaryCards.value.forEach(c => { c.value = counts[c.key] || 0 })
}

function onStatusTab(key) {
  filters.value.status = key
  page.value = 1
  fetchLongTasks()
}

function onSearch() {
  page.value = 1
  fetchLongTasks()
}

function prevPage() {
  if (page.value > 1) { page.value -= 1; fetchLongTasks() }
}

function nextPage() {
  if (page.value < totalPages.value) { page.value += 1; fetchLongTasks() }
}

// ---------- 取消与详情 ----------
const drawerVisible = ref(false)
const selectedTask = ref(null)

function openDetail(t) {
  selectedTask.value = t
  drawerVisible.value = true
}

async function onCancel(t) {
  cancelling.value[t.id] = true
  try {
    await cancelTask(t.id)
    await fetchLongTasks()
  } finally {
    cancelling.value[t.id] = false
  }
}

// ---------- 实时更新（SSE 替代轮询） ----------
// 长任务中心不再定时轮询；进入页面时拉取历史/分页数据，之后由全局 SSE 实时推送更新进度。
// 监听 store 中长任务变更，实时合并到列表（无需轮询即可看到进度跳动）。
watch(
  () => taskStore.tasks.map((t) => `${t.id}:${t.progress}:${t.status}`).join(','),
  () => mergeLiveTasks(),
)

onMounted(() => {
  fetchLongTasks()
})

onBeforeUnmount(() => {
  // 仅清理本地状态，SSE 连接由 AppLayout 统一维护
})
</script>
