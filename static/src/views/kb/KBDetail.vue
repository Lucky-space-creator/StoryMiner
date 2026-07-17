<template>
  <div class="space-y-6" v-if="kb">
    <div class="flex items-start justify-between gap-4">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push('/knowledge-bases')">
          ← 返回知识库
        </button>
        <h2 class="text-xl font-semibold text-app">{{ kb.name }}</h2>
        <p class="text-sm text-muted mt-1">小说：{{ kb.novel_name }} · 范围：{{ scopeText(kb.scope) }}</p>
      </div>
      <div class="flex gap-2 shrink-0">
        <Button variant="secondary" @click="openChunk">触发切割</Button>
        <Button variant="secondary" @click="runReindex">重建索引</Button>
      </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <Card v-for="s in statCards" :key="s.label">
        <p class="text-sm text-muted">{{ s.label }}</p>
        <p class="text-2xl font-semibold text-app mt-1">{{ s.value }}</p>
      </Card>
    </div>

    <Card>
      <h3 class="font-medium text-app mb-3">文档列表</h3>
      <Table :columns="docColumns" :rows="documents">
        <template #cell-chars="{ value }">
          <span class="text-muted">{{ value >= 10000 ? (value / 10000).toFixed(1) + '万字' : value + '字' }}</span>
        </template>
      </Table>
    </Card>

    <Card>
      <h3 class="font-medium text-app mb-3">切片策略</h3>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div><p class="text-muted">策略</p><p class="text-app font-semibold">{{ strategyLabel }}</p></div>
        <div><p class="text-muted">切片大小</p><p class="text-app font-semibold">{{ kb.chunk_size || 512 }} tokens</p></div>
        <div><p class="text-muted">重叠</p><p class="text-app font-semibold">{{ kb.chunk_overlap ?? 64 }} tokens</p></div>
        <div><p class="text-muted">向量模型</p><p class="text-app font-semibold">text-embedding-3</p></div>
      </div>
    </Card>

    <!-- 切割方式选择弹窗 -->
    <Modal v-model="chunkOpen" title="选择切割方式">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-3">
          <button
            v-for="opt in strategies"
            :key="opt.key"
            @click="form.strategy = opt.key"
            class="text-left p-3 rounded-[var(--radius-sm)] border transition"
            :class="form.strategy === opt.key ? 'border-accent bg-surface2' : 'border-app hover:border-accent'"
          >
            <p class="text-sm font-medium text-app">{{ opt.label }}</p>
            <p class="text-xs text-muted mt-1 leading-relaxed">{{ opt.desc }}</p>
          </button>
        </div>

        <div v-if="activeStrategy" class="rounded-[var(--radius-sm)] border border-app bg-surface2 p-3">
          <p class="text-sm font-medium text-app mb-1">{{ activeStrategy.label }} · 详细配置</p>
          <p class="text-xs text-muted leading-relaxed mb-3">{{ activeStrategy.detail }}</p>
          <div class="grid grid-cols-3 gap-3">
            <div>
              <label class="block text-xs text-muted mb-1">切片大小 (tokens)</label>
              <input
                v-model.number="form.size"
                type="number"
                min="64"
                step="64"
                class="w-full px-2 py-1.5 text-sm rounded-[var(--radius-sm)] border border-app bg-surface text-app focus:border-accent outline-none"
              />
            </div>
            <div>
              <label class="block text-xs text-muted mb-1">重叠 (tokens)</label>
              <input
                v-model.number="form.overlap"
                type="number"
                min="0"
                step="16"
                class="w-full px-2 py-1.5 text-sm rounded-[var(--radius-sm)] border border-app bg-surface text-app focus:border-accent outline-none"
              />
            </div>
            <div>
              <label class="block text-xs text-muted mb-1">向量模型</label>
              <select
                v-model="form.embedding"
                class="w-full px-2 py-1.5 text-sm rounded-[var(--radius-sm)] border border-app bg-surface text-app focus:border-accent outline-none"
              >
                <option value="text-embedding-3">text-embedding-3</option>
                <option value="bge-large-zh">bge-large-zh</option>
                <option value="m3e-base">m3e-base</option>
              </select>
            </div>
          </div>
          <p class="text-xs text-muted mt-3">
            预计生成切片数（按当前文档量估算）：<span class="text-accent font-semibold">{{ estChunks }} 段</span>
          </p>
        </div>

        <div class="flex justify-end gap-2 pt-1">
          <Button variant="ghost" @click="chunkOpen = false">取消</Button>
          <Button variant="primary" @click="runChunk">开始切割</Button>
        </div>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Table from '@/components/ui/Table.vue'
import Modal from '@/components/ui/Modal.vue'
import { getKB, getKBStats, getKBDocuments, chunkKB, reindexKB, getKBChunkProgress } from '@/api/knowledgeBases'
import { useToast } from '@/composables/useToast'
import { useTaskProgressStore } from '@/stores/taskProgress'

const route = useRoute()
const kb = ref(null)
const stats = ref({})
const documents = ref([])
const docColumns = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: '名称' },
  { key: 'chars', label: '字符量' },
  { key: 'added_at', label: '加入时间' }
]

const scopeMap = { full: '全本', custom: '自定义' }
const scopeText = (s) => scopeMap[s] || s
const statCards = ref([])

const strategies = [
  { key: 'semantic', label: '按章节语义', desc: '依据章节与语义边界自动切分', detail: '优先在章节、段落、语义转折处断句，保留上下文完整性，最适合小说正文与对话场景。' },
  { key: 'fixed', label: '固定长度', desc: '按固定 token 数连续切分', detail: '无视语义边界，按固定 token 窗口滑动切分，简单可控，适合结构松散的文档。' },
  { key: 'paragraph', label: '按段落', desc: '以自然段落为单位', detail: '以空行/换行划分的段落为最小单元，跨段合并至窗口上限，兼顾可读性与检索精度。' },
  { key: 'recursive', label: '递归字符', desc: '按分隔符递归切分', detail: '依次按 \n\n、\n、句号、逗号递归切分，尽量不打断句子，通用性强。' }
]
const strategyLabelMap = Object.fromEntries(strategies.map((s) => [s.key, s.label]))
const strategyLabel = computed(() => strategyLabelMap[kb.value?.chunk_strategy] || '按章节语义')

const chunkOpen = ref(false)
const form = ref({ strategy: 'semantic', size: 512, overlap: 64, embedding: 'text-embedding-3' })
const taskStore = useTaskProgressStore()
let progressTimer = null
const activeStrategy = computed(() => strategies.find((s) => s.key === form.value.strategy))

const estChunks = computed(() => {
  const chars = stats.value.chars || 36000
  const perChunk = (form.value.size || 512) * 1.4
  return Math.max(1, Math.round(chars / perChunk))
})

onMounted(async () => {
  const [kbRes, statsRes, docsRes] = await Promise.all([
    getKB(route.params.id),
    getKBStats(route.params.id),
    getKBDocuments(route.params.id)
  ])
  kb.value = kbRes.data || { name: '示例知识库', novel_name: '未知', scope: 'full' }
  stats.value = statsRes.data || {}
  documents.value = docsRes.data?.list || []
  if (kb.value.chunk_strategy) form.value.strategy = kb.value.chunk_strategy
  if (kb.value.chunk_size) form.value.size = kb.value.chunk_size
  if (kb.value.chunk_overlap != null) form.value.overlap = kb.value.chunk_overlap
  statCards.value = [
    { label: '文档数', value: stats.value.doc_count ?? 0 },
    { label: '切片数', value: stats.value.chunk_count ?? 0 },
    { label: '字符量', value: formatChars(stats.value.chars ?? 0) },
    { label: '更新时间', value: stats.value.updated_at ?? '-' }
  ]
})

onBeforeUnmount(() => {
  if (progressTimer) {
    clearInterval(progressTimer)
    progressTimer = null
  }
})

function formatChars(n) {
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万字` : `${n}字`
}

const { notify } = useToast()

function openChunk() {
  chunkOpen.value = true
}

const stageText = (s) =>
  ({ preparing: '准备中', chunking: '切分文档', embedding: '向量化中', storing: '写入索引', done: '完成', failed: '失败' }[s] || s || '处理中')

async function reloadStats() {
  const statsRes = await getKBStats(route.params.id)
  stats.value = statsRes.data || {}
  statCards.value = [
    { label: '文档数', value: stats.value.doc_count ?? 0 },
    { label: '切片数', value: stats.value.chunk_count ?? 0 },
    { label: '字符量', value: formatChars(stats.value.chars ?? 0) },
    { label: '更新时间', value: stats.value.updated_at ?? '-' }
  ]
}

function pollChunkProgress(taskId) {
  if (progressTimer) clearInterval(progressTimer)
  progressTimer = setInterval(async () => {
    try {
      const res = await getKBChunkProgress(route.params.id, taskId)
      const p = res.data || {}
      taskStore.upsert({ id: taskId, name: `切割：${kb.value?.name || ''}`, progress: p.progress || 0, stage: stageText(p.stage) })
      if (p.status === 'success') {
        clearInterval(progressTimer); progressTimer = null
        taskStore.upsert({ id: taskId, progress: 100, stage: '完成' })
        await reloadStats()
        await reloadDocuments()
        notify(p.message || `切割完成，共 ${p.chunk_count ?? 0} 段。`, 'success')
        setTimeout(() => taskStore.hide(), 1500)
      } else if (p.status === 'failed') {
        clearInterval(progressTimer); progressTimer = null
        taskStore.upsert({ id: taskId, stage: '失败：' + (p.error || '') })
        notify('切割失败：' + (p.error || '未知错误'), 'error')
      }
    } catch (e) {
      // 轮询异常不中断，等待下次重试
    }
  }, 1000)
}

async function runChunk() {
  try {
    const res = await chunkKB(route.params.id, { ...form.value })
    const taskId = res.data?.task_id
    kb.value.chunk_strategy = form.value.strategy
    kb.value.chunk_size = form.value.size
    kb.value.chunk_overlap = form.value.overlap
    chunkOpen.value = false
    if (!taskId) {
      notify('已触发切割（无进度跟踪）', 'success')
      return
    }
    taskStore.upsert({ id: taskId, name: `切割：${kb.value.name}`, progress: 0, stage: '准备中' })
    taskStore.show()
    pollChunkProgress(taskId)
  } catch (e) {
    notify(e?.message || '切割触发失败', 'error')
  }
}

async function reloadDocuments() {
  const docsRes = await getKBDocuments(route.params.id)
  documents.value = docsRes.data?.list || []
}

async function runReindex() {
  // 整体思路：触发全量重建索引（先清旧切片再重切），完成后刷新统计与文档列表。
  // 关键点：reindex 为同步接口，需 try/catch 容错，避免 500 时界面无反馈。
  // 实现逻辑：调接口 → 成功刷新 stats/documents 并提示；失败弹错。
  try {
    const res = await reindexKB(route.params.id)
    const d = res.data || {}
    stats.value = d
    statCards.value = [
      { label: '文档数', value: d.doc_count ?? 0 },
      { label: '切片数', value: d.chunk_count ?? 0 },
      { label: '字符量', value: formatChars(d.chars ?? 0) },
      { label: '更新时间', value: d.updated_at ?? '-' }
    ]
    await reloadDocuments()
    notify(`重建索引完成，共 ${d.chunk_count ?? 0} 段。`, 'success')
  } catch (e) {
    notify(e?.message || '重建索引失败', 'error')
  }
}
</script>
