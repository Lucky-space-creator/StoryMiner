<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">文档切片</h2>
      <Input v-model="search" placeholder="搜索切片内容…" class="w-64" @keyup.enter="load" />
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <select
        v-model="filters.kb_id"
        @change="load"
        class="bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
      >
        <option value="">全部知识库</option>
        <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
      </select>
      <input
        v-model.number="filters.chapter_id"
        type="number"
        min="1"
        placeholder="章节ID（数字）"
        class="bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent w-44"
        @keyup.enter="load"
      />
      <label class="flex items-center gap-2 text-sm text-muted">
        <input type="checkbox" v-model="filters.onlyDisabled" @change="load" class="accent-[var(--accent)]" />
        仅看已屏蔽
      </label>
      <Button variant="ghost" @click="reset">重置</Button>
    </div>

    <div v-if="loading" class="space-y-2">
      <div v-for="i in 6" :key="i" class="bg-surface border border-app rounded-[var(--radius-md)] p-4">
        <Skeleton h="0.875rem" w="80%" />
        <Skeleton h="0.75rem" w="50%" class="mt-3" />
      </div>
    </div>

    <EmptyState
      v-else-if="chunks.length === 0"
      title="没有匹配的切片"
      desc="调整筛选条件或切换知识库试试"
      :icon="PhParagraph"
    />

    <Table v-else :columns="columns" :rows="chunks">
      <template #cell-content="{ value }">
        <span class="line-clamp-1 text-muted">{{ value }}</span>
      </template>
      <template #cell-disabled="{ value }">
        <span :style="{ color: value ? 'var(--danger)' : 'var(--success)' }">
          {{ value ? '已屏蔽' : '启用' }}
        </span>
      </template>
      <template #cell-act="{ row }">
        <Button variant="ghost" class="!py-1 !px-2" @click="open(row)">查看</Button>
      </template>
    </Table>

    <Pagination
      v-if="!loading && chunks.length"
      :page="page" :size="size" :total="total"
      @update:page="(p) => { page = p; load() }"
      @update:size="(s) => { size = s; page = 1; load() }"
    />

    <Drawer v-model="openDetail" :title="`切片 #${current?.id ?? ''}`">
      <div v-if="current" class="space-y-4">
        <div class="text-sm text-muted">
          来源：{{ current.kb_name }} · {{ current.chapter }} · {{ current.chars }} 字
        </div>
        <div
          v-if="!editing"
          class="bg-surface2 border border-app rounded-[var(--radius-md)] p-4 text-sm text-app leading-relaxed max-h-80 overflow-y-auto whitespace-pre-wrap"
        >
          {{ current.content }}
        </div>
        <textarea
          v-else
          v-model="editForm.content"
          rows="18"
          class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent transition font-mono text-xs"
        ></textarea>
        <div class="flex items-center justify-between">
          <span class="text-sm text-muted">状态</span>
          <Tag :label="current.disabled ? '已屏蔽' : '启用'" />
        </div>
        <div class="flex justify-end gap-2">
          <template v-if="!editing">
            <Button variant="ghost" @click="editing = true">编辑</Button>
            <Button variant="ghost" @click="toggle">{{ current.disabled ? '启用' : '屏蔽' }}</Button>
            <Button @click="openDetail = false">关闭</Button>
          </template>
          <template v-else>
            <Button variant="ghost" @click="cancelEdit">取消</Button>
            <Button :loading="saving" @click="saveChunk">保存</Button>
          </template>
        </div>
      </div>
    </Drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { PhParagraph } from '@phosphor-icons/vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Table from '@/components/ui/Table.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Tag from '@/components/ui/Tag.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Pagination from '@/components/ui/Pagination.vue'
import { listChunks, getChunk, toggleChunk, searchChunks, updateChunk } from '@/api/chunks'
import { listKBs } from '@/api/knowledgeBases'
import { useToast } from '@/composables/useToast'

// 文档切片浏览（M4）：筛选/检索/分页查看，详情抽屉内可编辑内容并触发重向量化。
// 整体思路：
//   进入页面加载知识库下拉与首屏分页切片；支持关键词检索（同样分页）；
//   点击「查看」拉详情到抽屉，可切换编辑模式保存（M4.6 重向量化）。
// 关键点：
//   1. 列表与检索均走分页契约 {list,total,page,size}，前端维护 page/size/total。
//   2. 编辑保存调用 updateChunk，成功后回写本地行，避免整表刷新丢失筛选。
// 实现逻辑：
//   调 api/chunks 真实接口；Pagination 受控翻页；抽屉 reactive 表单双向绑定。
const { notify } = useToast()
const chunks = ref([])
const kbs = ref([])
const loading = ref(true)
const search = ref('')
const filters = ref({ kb_id: '', chapter_id: '', onlyDisabled: false })
const openDetail = ref(false)
const current = ref(null)
const editing = ref(false)
const saving = ref(false)
const editForm = ref({ content: '' })

// 分页状态
const page = ref(1)
const size = ref(20)
const total = ref(0)

const columns = [
  { key: 'seq', label: '序号' },
  { key: 'kb_name', label: '知识库' },
  { key: 'chapter', label: '章节' },
  { key: 'content', label: '内容' },
  { key: 'chars', label: '字数' },
  { key: 'disabled', label: '状态' },
  { key: 'act', label: '操作' }
]

onMounted(async () => {
  const [kbRes] = await Promise.all([listKBs()])
  kbs.value = kbRes.data?.list || []
  await load()
})

async function load() {
  // 整体思路：拉取切片列表/检索，支持筛选、关键词与分页。
  // 关键点：解析分页契约 {list,total,page,size}；异常统一 notify。
  // 实现逻辑：组装 params（含 page/size）→ 检索或列表 → 赋值分页状态。
  loading.value = true
  try {
    const params = { page: page.value, size: size.value }
    if (filters.value.kb_id) params.kb_id = filters.value.kb_id
    if (filters.value.chapter_id) params.chapter_id = filters.value.chapter_id
    if (filters.value.onlyDisabled) params.disabled = 'true'
    let res
    if (search.value.trim()) {
      res = await searchChunks(search.value.trim(), { page: page.value, size: size.value })
    } else {
      res = await listChunks(params)
    }
    const d = res.data || {}
    chunks.value = d.list || []
    total.value = d.total || 0
    page.value = d.page || page.value
    size.value = d.size || size.value
  } catch (e) {
    notify(e?.message || '加载切片失败', 'error')
  } finally {
    loading.value = false
  }
}

function reset() {
  filters.value = { kb_id: '', chapter_id: '', onlyDisabled: false }
  search.value = ''
  page.value = 1
  load()
}

async function open(row) {
  // 实现逻辑：打开详情前先请求后端完整内容，失败弹窗提示。
  try {
    const res = await getChunk(row.id)
    current.value = res.data || row
    editForm.value.content = current.value?.content || ''
    editing.value = false
    openDetail.value = true
  } catch (e) {
    notify(e?.message || '加载切片详情失败', 'error')
  }
}

async function toggle() {
  // 整体思路：屏蔽/启用切片并即时反馈结果。
  // 实现逻辑：计算目标状态 → 调接口 → 本地状态同步 → 成功/失败均弹窗。
  if (!current.value) return
  const next = !current.value.disabled
  try {
    await toggleChunk(current.value.id, next)
    current.value.disabled = next
    const target = chunks.value.find((c) => c.id === current.value.id)
    if (target) target.disabled = next
    notify(next ? '已屏蔽该切片' : '已启用该切片', 'success')
  } catch (e) {
    notify(e?.message || '操作失败', 'error')
  }
}

// 取消编辑：回滚表单到查看态。
function cancelEdit() {
  editForm.value.content = current.value?.content || ''
  editing.value = false
}

// 保存编辑：调 updateChunk 触发重向量化，成功后回写本地行。
async function saveChunk() {
  if (!current.value) return
  saving.value = true
  try {
    await updateChunk(current.value.id, { content: editForm.value.content })
    current.value.content = editForm.value.content
    current.value.word_count = editForm.value.content.length
    current.value.chars = editForm.value.content.length
    const target = chunks.value.find((c) => c.id === current.value.id)
    if (target) {
      target.content = editForm.value.content
      target.word_count = editForm.value.content.length
      target.chars = editForm.value.content.length
    }
    editing.value = false
    notify('切片已保存并重向量化', 'success')
  } catch (e) {
    notify(e?.message || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}
</script>
