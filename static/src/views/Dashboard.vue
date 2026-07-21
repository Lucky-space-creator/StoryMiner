<template>
  <div class="space-y-6">
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <Card v-for="s in statCards" :key="s.label">
        <p class="text-sm text-muted">{{ s.label }}</p>
        <p class="text-2xl font-semibold text-app mt-1">{{ s.value }}</p>
      </Card>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card>
        <h3 class="font-medium text-app mb-3">Token 消费趋势</h3>
        <div ref="trendEl" class="h-64"></div>
      </Card>
      <Card>
        <h3 class="font-medium text-app mb-3">模型调用占比</h3>
        <div ref="modelEl" class="h-64"></div>
      </Card>
    </div>

    <Card>
      <h3 class="font-medium text-app mb-3">Token 消费总览</h3>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div><p class="text-muted">输入 Token</p><p class="text-app font-semibold">{{ usage.tokensIn }}</p></div>
        <div><p class="text-muted">输出 Token</p><p class="text-app font-semibold">{{ usage.tokensOut }}</p></div>
        <div><p class="text-muted">调用次数</p><p class="text-app font-semibold">{{ usage.calls }}</p></div>
        <div><p class="text-muted">估算费用</p><p class="text-app font-semibold">¥{{ usage.cost }}</p></div>
      </div>
    </Card>

    <Card>
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-medium text-app">异步任务进度总览</h3>
        <div class="flex gap-4 text-sm">
          <span class="text-muted">进行中 <b class="text-amber-500">{{ taskSummary.running }}</b></span>
          <span class="text-muted">成功 <b class="text-emerald-600">{{ taskSummary.success }}</b></span>
          <span class="text-muted">失败 <b class="text-red-600">{{ taskSummary.failed }}</b></span>
          <span class="text-muted">已取消 <b class="text-stone-500">{{ taskSummary.cancelled }}</b></span>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 mb-3">
        <input
          v-model="filters.novelName" @keyup.enter="onSearch"
          placeholder="小说名模糊查询" 
          class="text-sm border border-stone-200 rounded px-2 py-1 bg-surface text-app outline-none focus:border-teal-400 w-44"
        />
        <select
          v-model="filters.completed" @change="onSearch"
          class="text-sm border border-stone-200 rounded px-2 py-1 bg-surface text-app outline-none focus:border-teal-400"
        >
          <option value="">全部状态</option>
          <option value="false">进行中</option>
          <option value="true">已完成</option>
        </select>
        <Button size="sm" @click="onSearch">查询</Button>
        <Button size="sm" variant="ghost" @click="onReset">重置</Button>
      </div>
      <div class="space-y-2">
        <div v-for="t in tasks" :key="t.id" class="border border-stone-200 rounded p-3 cursor-pointer hover:border-teal-400 transition" @click="openDetail(t)">
          <div class="flex items-center justify-between text-sm gap-2">
            <span class="font-medium text-app truncate">{{ t.name || ('任务 #' + t.id) }}</span>
            <span class="flex items-center gap-2 shrink-0">
              <span class="text-xs px-1.5 py-0.5 rounded bg-surface2 text-accent">{{ typeText(t.type) }}</span>
              <span :class="statusClass(t.status)">{{ statusLabel(t.status) }}</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-xs text-muted mt-1">
            <span>阶段：{{ stageLabel(t.stage) }}</span>
            <span>·</span>
            <span>{{ t.started_at ? '起 ' + fmt(t.started_at) : '未开始' }}</span>
            <span>·</span>
            <span>{{ t.finished_at ? '止 ' + fmt(t.finished_at) : '—' }}</span>
          </div>
          <div class="mt-2 h-2 bg-stone-100 rounded overflow-hidden">
            <div class="h-full bg-teal-600 transition-all" :style="{ width: t.progress + '%' }"></div>
          </div>
          <p v-if="t.error" class="mt-1 text-xs text-red-600 truncate" :title="t.error">{{ t.error }}</p>
          <p v-if="t.tokens_in || t.tokens_out" class="mt-1 text-xs text-muted">Token：{{ t.tokens_in }} 入 / {{ t.tokens_out }} 出</p>
          <div class="mt-2 flex justify-end">
            <Button v-if="t.status === 'running'" size="sm" variant="ghost" :disabled="cancelling[t.id]" @click.stop="onCancel(t)">
              {{ cancelling[t.id] ? '取消中…' : '取消任务' }}
            </Button>
          </div>
        </div>
          <p v-if="!tasks.length" class="text-sm text-muted text-center py-4">暂无异步任务</p>
      </div>
      <div v-if="total > 0" class="flex items-center justify-between mt-3 text-sm">
        <span class="text-muted">共 {{ total }} 条 · 第 {{ page }}/{{ totalPages }} 页</span>
        <div class="flex gap-2">
          <Button size="sm" variant="ghost" :disabled="page <= 1" @click="prevPage">上一页</Button>
          <Button size="sm" variant="ghost" :disabled="page >= totalPages" @click="nextPage">下一页</Button>
        </div>
      </div>
    </Card>

    <Drawer v-model="drawerVisible" :title="`任务 #${selectedTask?.id ?? ''} 详情`">
      <div v-if="selectedTask" class="space-y-4 text-sm">
        <div class="grid grid-cols-3 gap-2">
          <div><p class="text-muted">状态</p><p :class="statusClass(selectedTask.status)">{{ statusLabel(selectedTask.status) }}</p></div>
          <div><p class="text-muted">阶段</p><p class="text-app">{{ stageLabel(selectedTask.stage) }}</p></div>
          <div><p class="text-muted">进度</p><p class="text-app">{{ selectedTask.progress }}%</p></div>
          <div><p class="text-muted">Token 消耗</p><p class="text-app">{{ (selectedTask.tokens_in || 0) }} 入 / {{ (selectedTask.tokens_out || 0) }} 出</p></div>
        </div>
        <div class="h-2 bg-stone-100 rounded overflow-hidden">
          <div class="h-full bg-teal-600 transition-all" :style="{ width: selectedTask.progress + '%' }"></div>
        </div>
        <div class="space-y-1">
          <p class="text-muted">小说 ID：<span class="text-app">{{ selectedTask.novel_id ?? '—' }}</span></p>
          <p class="text-muted">文档 ID：<span class="text-app">{{ selectedTask.doc_id ?? '—' }}</span></p>
          <p class="text-muted">开始：<span class="text-app">{{ selectedTask.started_at ? fmt(selectedTask.started_at) : '—' }}</span></p>
          <p class="text-muted">结束：<span class="text-app">{{ selectedTask.finished_at ? fmt(selectedTask.finished_at) : '—' }}</span></p>
          <p class="text-muted">创建：<span class="text-app">{{ selectedTask.created_at ? fmt(selectedTask.created_at) : '—' }}</span></p>
        </div>
        <div>
          <p class="text-muted mb-1">错误原因</p>
          <pre v-if="selectedTask.error" class="whitespace-pre-wrap break-words bg-stone-50 border border-stone-200 rounded p-2 text-red-600 text-xs">{{ selectedTask.error }}</pre>
          <p v-else class="text-app text-xs">无</p>
        </div>
        <Button v-if="selectedTask.status === 'failed' && selectedTask.type === 'parse'" :disabled="retrying" @click="onRetry(selectedTask.id)">
          {{ retrying ? '重试中…' : '一键重试' }}
        </Button>
        <Button v-if="selectedTask.status === 'running'" variant="ghost" :disabled="cancelling[selectedTask.id]" @click="onCancel(selectedTask)">
          {{ cancelling[selectedTask.id] ? '取消中…' : '取消任务' }}
        </Button>
      </div>
    </Drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import Card from '@/components/ui/Card.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Button from '@/components/ui/Button.vue'
import { getStats, getTokenUsage, getTokenTrend, getModelStats, getTaskOverview } from '@/api/dashboard'
import { retryParseTask } from '@/api/parseTasks'
import { cancelTask } from '@/api/tasks'
import { typeText } from '@/utils/taskStages'

const statCards = ref([])
const usage = ref({ tokensIn: 0, tokensOut: 0, calls: 0, cost: 0 })
const tasks = ref([])
const taskSummary = ref({ total: 0, running: 0, success: 0, failed: 0, cancelled: 0 })
const cancelling = ref({})

// 任务列表筛选与分页
const filters = ref({ novelName: '', completed: '' })
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

async function onCancel(t) {
  cancelling.value[t.id] = true
  try {
    await cancelTask(t.id)
    await refreshTasks()
  } finally {
    cancelling.value[t.id] = false
  }
}

async function refreshTasks() {
  const params = { page: page.value, page_size: pageSize.value }
  if (filters.value.novelName.trim()) params.novel_name = filters.value.novelName.trim()
  if (filters.value.completed !== '') params.completed = filters.value.completed === 'true'
  const res = await getTaskOverview(params)
  tasks.value = res.data.tasks || []
  taskSummary.value = res.data.summary || { total: 0, running: 0, success: 0, failed: 0, cancelled: 0 }
  total.value = res.data.total || 0
}

function onSearch() {
  page.value = 1
  refreshTasks()
}

function onReset() {
  filters.value = { novelName: '', completed: '' }
  page.value = 1
  refreshTasks()
}

function prevPage() {
  if (page.value > 1) {
    page.value -= 1
    refreshTasks()
  }
}

function nextPage() {
  if (page.value < totalPages.value) {
    page.value += 1
    refreshTasks()
  }
}
const drawerVisible = ref(false)
const selectedTask = ref(null)
const retrying = ref(false)
const trendEl = ref(null)
const modelEl = ref(null)
let trendChart = null
let modelChart = null
let taskTimer = null

function openDetail(t) {
  selectedTask.value = t
  drawerVisible.value = true
}

async function onRetry(taskId) {
  // 仅解析类任务支持一键重试（复用既有 /parse-tasks 重试接口，按 extra.parse_task_id 定位）
  retrying.value = true
  try {
    const parseTaskId = selectedTask.value?.extra?.parse_task_id || taskId
    await retryParseTask(parseTaskId)
    drawerVisible.value = false
    await refreshTasks()
  } finally {
    retrying.value = false
  }
}

const STAGE_LABEL = {
  pending: '排队中', preparing: '准备中', parsing: '解析中', splitting: '切章中',
  chunking: '切分文档', embedding: '向量化中', storing: '写入索引',
  extracting: '抽取实体中', generating: '生成小传中', done: '已完成', failed: '失败',
  cancelled: '已取消',
}
const STATUS_LABEL = { running: '进行中', success: '成功', failed: '失败', cancelled: '已取消' }
function stageLabel(s) { return STAGE_LABEL[s] || s || '未知' }
function statusLabel(s) { return STATUS_LABEL[s] || s || '未知' }
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

function renderTrend(data) {
  if (!trendEl.value) return
  trendChart = echarts.init(trendEl.value)
  trendChart.setOption({
    grid: { left: 40, right: 16, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: data.map((d) => d.date), axisLine: { lineStyle: { color: '#78716c' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#e7e5e4' } } },
    series: [{ type: 'line', smooth: true, data: data.map((d) => d.tokens), itemStyle: { color: '#0d9488' }, areaStyle: { color: 'rgba(13,148,136,0.12)' } }]
  })
}

function renderModel(data) {
  if (!modelEl.value) return
  modelChart = echarts.init(modelEl.value)
  modelChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: '#78716c' } },
    series: [{
      type: 'pie',
      radius: ['45%', '70%'],
      data: data.map((d) => ({ name: d.name, value: d.calls })),
      color: ['#0d9488', '#2dd4bf', '#d97706', '#dc2626']
    }]
  })
}

function resize() {
  trendChart?.resize()
  modelChart?.resize()
}

onMounted(async () => {
  const [stats, usageRes, trend, models] = await Promise.all([
    getStats(), getTokenUsage(), getTokenTrend(), getModelStats()
  ])
  const s = stats.data
  statCards.value = [
    { label: '小说', value: s.novels },
    { label: '知识库', value: s.knowledgeBases },
    { label: '切片', value: s.chunks },
    { label: '实体', value: s.entities }
  ]
  usage.value = usageRes.data
  renderTrend(trend.data)
  renderModel(models.data)
  await refreshTasks()
  window.addEventListener('resize', resize)
  // 每 5s 刷新异步任务总览（进行中任务进度/状态实时更新）
  taskTimer = setInterval(refreshTasks, 5000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  if (taskTimer) clearInterval(taskTimer)
  trendChart?.dispose()
  modelChart?.dispose()
})
</script>
