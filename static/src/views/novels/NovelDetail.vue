<template>
  <div class="space-y-6" v-if="novel">
    <div class="flex items-start justify-between gap-4">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push('/novels')">
          ← 返回小说
        </button>
        <h2 class="text-xl font-semibold text-app">{{ novel.name }}</h2>
        <p class="text-sm text-muted mt-1">作者：{{ novel.author }}</p>
        <div class="flex flex-wrap gap-1 mt-2">
          <Tag v-for="t in (novel.tags || [])" :key="t" :label="t" />
        </div>
      </div>
      <div class="flex gap-2 shrink-0">
        <input ref="docsEl" type="file" accept=".txt,.epub,.pdf,.docx" multiple class="hidden" @change="onFiles" />
        <Button variant="secondary" :loading="uploading" @click="docsEl?.click()">上传文档</Button>
        <Button @click="goKb">建知识库</Button>
        <Button variant="secondary" :loading="analyzing" @click="analyzeNovel">人物分析</Button>
        <Button variant="secondary" :loading="chapterAnalyzing" @click="triggerChapterAnalysis">章节解析</Button>
        <Button @click="goReadNovel">阅读小说</Button>
      </div>
    </div>

    <p class="text-sm text-app">{{ novel.summary || '（暂无简介）' }}</p>

    <!-- AI 概括：上传文档解析完成后由异步任务生成，展示在用户简介下方 -->
    <div v-if="novel.ai_summary" class="bg-surface2 border border-app rounded-[var(--radius-sm)] p-3">
      <div class="flex items-center gap-1.5 mb-1.5">
        <span class="text-xs font-medium text-accent">AI 概括</span>
        <span class="text-xs text-muted">· 由大模型基于小说正文生成</span>
      </div>
      <p class="text-sm text-app leading-relaxed">{{ novel.ai_summary }}</p>
    </div>

    <!-- 章节解析结果展示 -->
    <Card v-if="chapterAnalysis">
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-medium text-app">章节解析</h3>
        <Button variant="ghost" size="sm" @click="loadChapterAnalysis">刷新</Button>
      </div>
      <!-- 整体结构 -->
      <div v-if="chapterAnalysis.structure" class="bg-surface2 border border-app rounded-[var(--radius-sm)] p-3 mb-4">
        <p class="text-xs font-medium text-accent mb-1">整体结构分析</p>
        <p class="text-sm text-app">{{ chapterAnalysis.structure.overall_analysis || '暂无' }}</p>
        <div class="flex flex-wrap gap-2 mt-2 text-xs text-muted">
          <span>类型：{{ chapterAnalysis.structure.structure_type || '未知' }}</span>
          <span v-if="chapterAnalysis.structure.narrative_style">视角：{{ chapterAnalysis.structure.narrative_style }}</span>
          <span>总章节：{{ chapterAnalysis.total }}</span>
        </div>
        <!-- 分卷信息 -->
        <div v-if="chapterAnalysis.structure.volumes && chapterAnalysis.structure.volumes.length" class="mt-2 space-y-1">
          <p class="text-xs text-muted">分卷结构：</p>
          <div v-for="v in chapterAnalysis.structure.volumes" :key="v.title" class="text-xs text-app">
            <span class="font-medium">{{ v.title }}</span>
            <span class="text-muted">（第{{ v.chapter_range }}章）{{ v.summary }}</span>
          </div>
        </div>
      </div>
      <!-- 各章节分析列表（每章仅一条，只显示标题+摘要） -->
      <div v-if="chapterAnalysis.chapters && chapterAnalysis.chapters.length" class="space-y-1 max-h-80 overflow-y-auto">
        <p class="text-xs text-muted mb-2">共 {{ chapterAnalysis.total }} 章，已分析 {{ analyzedCount }} 章</p>
        <div v-for="ch in chapterAnalysis.chapters" :key="'ch_' + ch.chapter_no" class="flex items-start gap-2 py-1.5 border-b border-stone-100 last:border-0 text-sm">
          <span class="text-xs text-muted shrink-0 w-12 text-right">{{ ch.chapter_no }}</span>
          <div class="min-w-0 flex-1">
            <p class="text-app truncate">{{ ch.title || ('第' + ch.chapter_no + '章') }}</p>
            <p v-if="ch.analysis?.summary" class="text-muted text-xs mt-0.5 line-clamp-1">{{ ch.analysis.summary }}</p>
            <p v-else class="text-xs text-muted italic mt-0.5">待分析</p>
          </div>
        </div>
      </div>
    </Card>

    <Card>
      <div class="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 class="font-medium text-app">已上传文档（{{ total }}）</h3>
        <Button variant="ghost" size="sm" :loading="loading" @click="loadDocs">刷新</Button>
      </div>

      <div v-if="loading" class="space-y-2">
        <Skeleton v-for="i in 4" :key="i" h="2.25rem" />
      </div>
      <Table v-else :columns="docColumns" :rows="documents">
        <template #cell-status="{ value }">
          <span :class="statusClass(value)">{{ statusText(value) }}</span>
        </template>
        <template #cell-actions="{ row }">
          <div class="flex gap-1">
            <Button variant="ghost" size="sm" @click="openRename(row)">重命名</Button>
            <Button variant="ghost" size="sm" class="text-danger" @click="askDelete(row)">删除</Button>
          </div>
        </template>
      </Table>

      <Pagination
        v-if="!loading && total > 0"
        :page="page" :size="size" :total="total"
        @update:page="(p) => { page = p; loadDocs() }"
        @update:size="(s) => { size = s; page = 1; loadDocs() }"
      />
      <EmptyState v-if="!loading && total === 0" title="还没有文档" desc="点击右上角「上传文档」添加 TXT/EPUB/PDF/DOCX" />
    </Card>

    <Modal v-model="renameOpen" title="重命名文档">
      <form @submit.prevent="submitRename" class="space-y-4">
        <Input v-model="renameForm.name" label="文档名称" placeholder="请输入名称" />
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="renameOpen = false">取消</Button>
          <Button type="submit" :loading="renaming">保存</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除文档" :message="`确定删除《${pending?.name}》？该文档在所有知识库的切片与向量将一并清除，且不可恢复。`" @confirm="doDelete" />
  </div>
</template>

<script setup>
// 小说详情（方案A）：仅展示简介 + 已上传文档的 CRUD 与分页；切章/切分在知识库构建阶段完成。
// 整体思路：进入页面拉取小说元信息与分页文档；上传触发异步解析，轮询刷新至全部完成。
// 关键点：
//   1. 文档归属小说，列表接口 GET /novels/{id}/documents 已分页。
//   2. 上传支持多文件，逐个解析；状态 pending/parsing/done/failed 实时反映。
//   3. 删除/重命名走 /documents/{id}，按 owner 隔离。
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Tag from '@/components/ui/Tag.vue'
import Table from '@/components/ui/Table.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Modal from '@/components/ui/Modal.vue'
import Input from '@/components/ui/Input.vue'
import Pagination from '@/components/ui/Pagination.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { getNovel, listNovelDocuments, uploadDocuments, deleteNovelDocument, renameNovelDocument, analyzeChapters, getChapterAnalysis } from '@/api/novels'
import { analyzeCharacters } from '@/api/characters'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const { notify } = useToast()
const novel = ref(null)
const documents = ref([])
const loading = ref(true)
const uploading = ref(false)
const analyzing = ref(false)
const chapterAnalyzing = ref(false)
const chapterAnalysis = ref(null)
const docsEl = ref(null)

// 分页状态
const page = ref(1)
const size = ref(20)
const total = ref(0)

// 重命名 / 删除状态
const renameOpen = ref(false)
const renaming = ref(false)
const renameForm = reactive({ id: null, name: '' })
const delOpen = ref(false)
const pending = ref(null)

const docColumns = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: '名称' },
  { key: 'doc_type', label: '类型' },
  { key: 'status', label: '状态' },
  { key: 'word_count', label: '字数' },
  { key: 'created_at', label: '上传时间' },
  { key: 'actions', label: '操作' }
]

const statusMap = { done: '已完成', parsing: '解析中', pending: '待解析', failed: '失败' }
function statusText(s) { return statusMap[s] || s }
function statusClass(s) {
  return {
    'text-accent': s === 'done',
    'text-warning': s === 'parsing' || s === 'pending',
    'text-danger': s === 'failed',
    'text-muted': !s,
  }
}

let pollTimer = null
function clearPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

async function loadDocs() {
  loading.value = true
  try {
    const res = await listNovelDocuments(route.params.id, { page: page.value, size: size.value })
    const d = res.data || {}
    documents.value = d.list || []
    total.value = d.total || 0
    page.value = d.page || page.value
    size.value = d.size || size.value
  } catch (e) {
    notify(e.message || '加载文档失败', 'error')
  } finally {
    loading.value = false
  }
}

// 解析中轮询：全部完成时停止；同时刷新小说详情以获取 AI 概括
function maybePoll() {
  const active = documents.value.some((d) => d.status === 'pending' || d.status === 'parsing')
  if (active && !pollTimer) {
    pollTimer = setInterval(async () => {
      await loadDocs()
      // 刷新小说详情（用于拿 AI 概括 ai_summary）
      try {
        const res = await getNovel(route.params.id)
        if (res.data) novel.value = { ...novel.value, ...res.data }
      } catch (e) { /* 忽略刷新失败 */ }
      if (!documents.value.some((d) => d.status === 'pending' || d.status === 'parsing')) {
        // 解析全部完成后再补一次小说详情，确保拿到最终 AI 概括
        try {
          const res = await getNovel(route.params.id)
          if (res.data) novel.value = { ...novel.value, ...res.data }
        } catch (e) { /* 忽略 */ }
        clearPoll()
      }
    }, 2000)
  }
}

function onFiles(e) {
  const files = Array.from(e.target.files || [])
  if (!files.length) return
  uploadAll(files)
  if (docsEl.value) docsEl.value.value = ''
}

async function uploadAll(files) {
  uploading.value = true
  try {
    await uploadDocuments(route.params.id, files)
    notify(`已开始解析 ${files.length} 个文件`, 'info')
    page.value = 1
    await loadDocs()
    maybePoll()
  } catch (e) {
    notify(e.message || '上传失败', 'error')
  } finally {
    uploading.value = false
  }
}

function goKb() {
  router.push({ path: '/knowledge-bases', query: { novel_id: novel.value?.id } })
}

async function analyzeNovel() {
  analyzing.value = true
  try {
    const res = await analyzeCharacters(route.params.id)
    notify(`人物分析已启动（任务 #${res.data?.task_id || '—'}），可在仪表盘查看进度`, 'info')
  } catch (e) {
    notify(e.message || '启动人物分析失败', 'error')
  } finally {
    analyzing.value = false
  }
}

// 章节解析
async function triggerChapterAnalysis() {
  chapterAnalyzing.value = true
  try {
    const res = await analyzeChapters(route.params.id)
    notify(`章节解析已启动（任务 #${res.data?.task_id || '—'}），可在仪表盘查看进度`, 'info')
  } catch (e) {
    notify(e.message || '启动章节解析失败', 'error')
  } finally {
    chapterAnalyzing.value = false
  }
}

async function loadChapterAnalysis() {
  try {
    const res = await getChapterAnalysis(route.params.id)
    chapterAnalysis.value = res.data || null
  } catch (e) {
    // 静默失败，章节分析数据可能尚未生成
  }
}

// 阅读小说：跳转到阅读页面
function goReadNovel() {
  router.push({ path: `/novels/${route.params.id}/read` })
}

const analyzedCount = computed(() => {
  if (!chapterAnalysis.value?.chapters) return 0
  return chapterAnalysis.value.chapters.filter(c => c.analysis).length
})

function openRename(row) {
  renameForm.id = row.id
  renameForm.name = row.name
  renameOpen.value = true
}

async function submitRename() {
  if (!renameForm.name.trim()) return
  renaming.value = true
  try {
    await renameNovelDocument(renameForm.id, renameForm.name.trim())
    renameOpen.value = false
    notify('已重命名', 'success')
    await loadDocs()
  } catch (e) {
    notify(e.message || '重命名失败', 'error')
  } finally {
    renaming.value = false
  }
}

function askDelete(row) {
  pending.value = row
  delOpen.value = true
}

async function doDelete() {
  if (!pending.value) return
  try {
    await deleteNovelDocument(pending.value.id)
    documents.value = documents.value.filter((x) => x.id !== pending.value.id)
    total.value = Math.max(0, total.value - 1)
    notify(`已删除《${pending.value.name}》`, 'success')
  } catch (e) {
    notify(e.message || '删除失败', 'error')
  } finally {
    pending.value = null
  }
}

onMounted(async () => {
  try {
    const res = await getNovel(route.params.id)
    novel.value = res.data || null
    await loadDocs()
    await loadChapterAnalysis()
  } catch (e) {
    notify(e.message || '加载失败', 'error')
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => clearPoll())
</script>
