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
        <Button variant="secondary" @click="openBuild">构建索引</Button>
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
        <template #cell-word_count="{ value }">
          <span class="text-muted">{{ value >= 10000 ? (value / 10000).toFixed(1) + '万字' : (value || 0) + '字' }}</span>
        </template>
        <template #cell-created_at="{ value }">
          <span class="text-muted">{{ value || '-' }}</span>
        </template>
      </Table>
    </Card>

    <Card>
      <h3 class="font-medium text-app mb-3">切片策略</h3>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div><p class="text-muted">策略</p><p class="text-app font-semibold">{{ strategyLabel }}</p></div>
        <div><p class="text-muted">切片大小</p><p class="text-app font-semibold">{{ kb.chunk_size || 512 }} tokens</p></div>
        <div><p class="text-muted">重叠</p><p class="text-app font-semibold">{{ kb.chunk_overlap ?? 64 }} tokens</p></div>
        <div><p class="text-muted">向量模型</p><p class="text-app font-semibold">{{ kb.embedding_model || '默认嵌入模型' }}</p></div>
      </div>
    </Card>

    <!-- 构建索引弹窗（方案A：单选文档） -->
    <Modal v-model="buildOpen" title="构建索引（选择文档）">
      <div class="space-y-4">
        <div>
          <label class="block text-sm text-app mb-2">选择参与构建的文档（单选）</label>
          <div v-if="buildLoading" class="text-sm text-muted">加载文档中…</div>
          <div v-else-if="buildDocs.length === 0" class="text-sm text-muted">该小说暂无已上传文档，请先到小说详情上传。</div>
          <div v-else class="space-y-2 max-h-48 overflow-auto">
            <label
              v-for="d in buildDocs"
              :key="d.id"
              class="flex items-center gap-2 p-2 rounded-[var(--radius-sm)] border cursor-pointer transition"
              :class="selectedDocId === d.id ? 'border-accent bg-surface2' : 'border-app hover:border-accent'"
            >
              <input type="radio" :value="d.id" v-model="selectedDocId" class="accent-[var(--accent)]" />
              <span class="text-sm text-app flex-1 truncate">{{ d.name }}</span>
              <span class="text-xs text-muted">{{ d.status }}</span>
            </label>
          </div>
        </div>

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
              <div v-if="embedModels.length === 0" class="text-xs text-muted">暂无可用嵌入模型，请先在「模型管理」添加 llm_type=embed 的配置</div>
              <select
                v-else
                v-model.number="selectedEmbedId"
                class="w-full px-2 py-1.5 text-sm rounded-[var(--radius-sm)] border border-app bg-surface text-app focus:border-accent outline-none"
              >
                <option v-for="m in embedModels" :key="m.id" :value="m.id">
                  {{ m.name }}（{{ m.model }}）{{ m.is_default ? '· 默认' : '' }}
                </option>
              </select>
            </div>
          </div>
        </div>

        <div class="flex justify-end gap-2 pt-1">
          <Button variant="ghost" @click="buildOpen = false">取消</Button>
          <Button variant="primary" :disabled="!selectedDocId" @click="runBuild">开始构建</Button>
        </div>
      </div>
    </Modal>

    <Card>
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-medium text-app">切片列表</h3>
        <span class="text-xs text-muted">共 {{ chunkTotal }} 个切片</span>
      </div>

      <div v-if="chunkLoading" class="space-y-2">
        <div v-for="i in 4" :key="i" class="bg-surface2 border border-app rounded-[var(--radius-sm)] p-3">
          <Skeleton h="0.875rem" w="80%" />
        </div>
      </div>

      <EmptyState
        v-else-if="chunkList.length === 0"
        title="暂无切片"
        desc="构建索引后，文档切片会在此分页展示"
        :icon="PhParagraph"
      />

      <Table v-else :columns="chunkColumns" :rows="chunkList">
        <template #cell-content="{ value }">
          <span class="line-clamp-1 text-muted">{{ value }}</span>
        </template>
        <template #cell-disabled="{ value }">
          <span :style="{ color: value ? 'var(--danger)' : 'var(--success)' }">
            {{ value ? '已屏蔽' : '启用' }}
          </span>
        </template>
        <template #cell-act="{ row }">
          <Button variant="ghost" class="!py-1 !px-2" @click="openChunk(row)">查看</Button>
        </template>
      </Table>

      <Pagination
        v-if="!chunkLoading && chunkList.length"
        :page="chunkPage" :size="chunkSize" :total="chunkTotal"
        @update:page="(p) => { chunkPage = p; loadChunks() }"
        @update:size="(s) => { chunkSize = s; chunkPage = 1; loadChunks() }"
      />
    </Card>

    <Drawer v-model="chunkOpen" :title="`切片 #${chunkCurrent?.id ?? ''}`">
      <div v-if="chunkCurrent" class="space-y-3">
        <div class="text-sm text-muted">
          {{ chunkCurrent.kb_name }} · {{ chunkCurrent.chapter }} · {{ chunkCurrent.chars }} 字
        </div>
        <div class="bg-surface2 border border-app rounded-[var(--radius-md)] p-4 text-sm text-app leading-relaxed max-h-96 overflow-y-auto whitespace-pre-wrap">
          {{ chunkCurrent.content }}
        </div>
        <div class="flex items-center justify-between text-sm">
          <span class="text-muted">状态</span>
          <Tag :label="chunkCurrent.disabled ? '已屏蔽' : '启用'" />
        </div>
      </div>
    </Drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Table from '@/components/ui/Table.vue'
import Modal from '@/components/ui/Modal.vue'
import Pagination from '@/components/ui/Pagination.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Tag from '@/components/ui/Tag.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { PhParagraph } from '@phosphor-icons/vue'
import { getKB, getKBStats, getKBDocuments, buildKB, reindexKB } from '@/api/knowledgeBases'
import { listNovelDocuments } from '@/api/novels'
import { listLlm } from '@/api/llmConfigs'
import { listChunks, getChunk } from '@/api/chunks'
import { formatDateTime } from '@/utils/datetime'
import { useToast } from '@/composables/useToast'
import { useTaskProgressStore } from '@/stores/taskProgress'

const route = useRoute()
const kb = ref(null)
const stats = ref({})
const documents = ref([])
const docColumns = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: '名称' },
  { key: 'word_count', label: '字符量' },
  { key: 'created_at', label: '加入时间' }
]

// 切片列表（底部分页展示）：按当前知识库过滤，复用 /chunks 分页契约。
const chunkList = ref([])
const chunkLoading = ref(false)
const chunkPage = ref(1)
const chunkSize = ref(20)
const chunkTotal = ref(0)
const chunkOpen = ref(false)
const chunkCurrent = ref(null)
const chunkColumns = [
  { key: 'id', label: 'ID' },
  { key: 'chapter', label: '章节' },
  { key: 'content', label: '内容' },
  { key: 'chars', label: '字数' },
  { key: 'disabled', label: '状态' },
  { key: 'act', label: '操作' }
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

const buildOpen = ref(false)
const buildDocs = ref([])
const embedModels = ref([])
const selectedDocId = ref(null)
const selectedEmbedId = ref(null)
const buildLoading = ref(false)
const form = ref({ strategy: 'semantic', size: 512, overlap: 64, embedding: 'text-embedding-3' })
const taskStore = useTaskProgressStore()
const myTaskId = ref(null)
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
  await loadChunks()
  if (kb.value.chunk_strategy) form.value.strategy = kb.value.chunk_strategy
  if (kb.value.chunk_size) form.value.size = kb.value.chunk_size
  if (kb.value.chunk_overlap != null) form.value.overlap = kb.value.chunk_overlap
  statCards.value = [
    { label: '文档数', value: stats.value.doc_count ?? 0 },
    { label: '切片数', value: stats.value.chunk_count ?? 0 },
    { label: '字符量', value: formatChars(stats.value.chars ?? 0) },
    { label: '更新时间', value: formatDateTime(stats.value.updated_at) }
  ]
})

onBeforeUnmount(() => {
  myTaskId.value = null
})

// 监听本页任务完成：由全局轮询更新 store，这里只负责刷新本页数据与文档列表
watch(
  () => taskStore.tasks.find((t) => t.id === myTaskId.value)?.status,
  async (s) => {
    if (!s || !myTaskId.value) return
    if (s === 'success') {
      await reloadStats()
      await reloadDocuments()
      await loadChunks()
      myTaskId.value = null
    } else if (s === 'failed') {
      myTaskId.value = null
    }
  }
)

function formatChars(n) {
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万字` : `${n}字`
}

const { notify } = useToast()

async function openBuild() {
  // 整体思路：打开构建弹窗并加载该小说下全部文档，供单选参与构建。
  // 关键点：文档归属小说，按 novel_id 拉取；构建时仅对选中文档切分向量化。
  buildOpen.value = true
  buildLoading.value = true
  selectedDocId.value = null
  selectedEmbedId.value = null
  try {
    const [docRes, embedRes] = await Promise.all([
      listNovelDocuments(kb.value.novel_id, { size: 100 }),
      listLlm({ llm_type: 'embed' }),
    ])
    buildDocs.value = docRes.data?.list || []
    embedModels.value = embedRes.data || []
    // 默认选中：优先默认模型，否则第一个
    const def = embedModels.value.find((m) => m.is_default) || embedModels.value[0]
    selectedEmbedId.value = def ? def.id : null
  } catch (e) {
    notify(e?.message || '加载文档失败', 'error')
    buildDocs.value = []
    embedModels.value = []
  } finally {
    buildLoading.value = false
  }
}

async function reloadStats() {
  const statsRes = await getKBStats(route.params.id)
  stats.value = statsRes.data || {}
  statCards.value = [
    { label: '文档数', value: stats.value.doc_count ?? 0 },
    { label: '切片数', value: stats.value.chunk_count ?? 0 },
    { label: '字符量', value: formatChars(stats.value.chars ?? 0) },
    { label: '更新时间', value: formatDateTime(stats.value.updated_at) }
  ]
}

async function runBuild() {
  // 整体思路：单选文档提交构建，后台切分向量化该文档并纳入知识库；触发后弹窗提示后台处理中，
  // 全局轮询负责进度刷新，本页 watcher 在任务完成时刷新统计与文档列表。
  if (!selectedDocId.value) return
  try {
    const res = await buildKB(route.params.id, {
      doc_id: selectedDocId.value,
      embed_config_id: selectedEmbedId.value,
      strategy: form.value.strategy,
      size: form.value.size,
      overlap: form.value.overlap,
    })
    const taskId = res.data?.task_id
    kb.value.chunk_strategy = form.value.strategy
    kb.value.chunk_size = form.value.size
    kb.value.chunk_overlap = form.value.overlap
    buildOpen.value = false
    if (!taskId) {
      notify('已触发构建（无进度跟踪）', 'success')
      return
    }
    myTaskId.value = taskId
    taskStore.upsert({ id: taskId, type: 'chunk', name: `小说${kb.value.novel_name}-构建索引`, progress: 0, stage: '已提交，后台处理中', status: 'running' })
    taskStore.show()
    // 索引构建统一走长任务中心
    notify(`索引构建已启动（任务 #${taskId}），预估耗时 ${res.data?.estimated_minutes || '?'} 分钟，请前往「长任务中心」（侧边栏可进入）查看进度`, 'info', 6000)
  } catch (e) {
    notify(e?.message || '构建触发失败', 'error')
  }
}

async function reloadDocuments() {
  const docsRes = await getKBDocuments(route.params.id)
  documents.value = docsRes.data?.list || []
}

async function loadChunks() {
  // 整体思路：按当前知识库分页拉取切片列表，复用 /chunks 的分页契约 {list,total,page,size}。
  // 关键点：kb_id 由路由参数提供；翻页/改每页大小后重新请求并回写分页状态。
  // 实现逻辑：组装 params → listChunks → 赋值 chunkList/total/page/size；异常统一 notify。
  if (!kb.value?.id) return
  chunkLoading.value = true
  try {
    const res = await listChunks({ kb_id: kb.value.id, page: chunkPage.value, size: chunkSize.value })
    const d = res.data || {}
    chunkList.value = d.list || []
    chunkTotal.value = d.total || 0
    chunkPage.value = d.page || chunkPage.value
    chunkSize.value = d.size || chunkSize.value
  } catch (e) {
    notify(e?.message || '加载切片失败', 'error')
  } finally {
    chunkLoading.value = false
  }
}

async function openChunk(row) {
  // 实现逻辑：打开详情前先请求后端完整内容，失败弹窗提示。
  try {
    const res = await getChunk(row.id)
    chunkCurrent.value = res.data || row
    chunkOpen.value = true
  } catch (e) {
    notify(e?.message || '加载切片详情失败', 'error')
  }
}

async function runReindex() {
  // 整体思路：触发全量重建索引（后台任务）；触发后弹窗提示后台处理中，全局轮询刷新进度，
  // 本页 watcher 在任务完成时刷新统计与文档列表。
  try {
    const res = await reindexKB(route.params.id, {
      embed_config_id: selectedEmbedId.value,
      strategy: form.value.strategy,
      size: form.value.size,
      overlap: form.value.overlap,
    })
    const taskId = res.data?.task_id
    if (!taskId) {
      notify('已触发重建（无进度跟踪）', 'success')
      return
    }
    myTaskId.value = taskId
    taskStore.upsert({ id: taskId, type: 'chunk', name: `小说${kb.value.novel_name}-重建索引`, progress: 0, stage: '已提交，后台处理中', status: 'running' })
    taskStore.show()
    // 索引重建统一走长任务中心
    notify(`索引重建已启动（任务 #${taskId}），预估耗时 ${res.data?.estimated_minutes || '?'} 分钟，请前往「长任务中心」（侧边栏可进入）查看进度`, 'info', 6000)
  } catch (e) {
    notify(e?.message || '重建索引失败', 'error')
  }
}
</script>
