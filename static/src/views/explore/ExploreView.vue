<template>
  <div class="space-y-4">
    <h2 class="text-lg font-semibold text-app">扩展功能</h2>

    <div class="flex gap-1 border-b border-app">
      <button
        v-for="t in tabs"
        :key="t.key"
        @click="tab = t.key"
        class="px-3 py-2 text-sm border-b-2 transition"
        :class="tab === t.key ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-app'"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- 全局搜索 -->
    <div v-if="tab === 'search'" class="space-y-3">
      <div class="flex gap-2">
        <Input v-model="search" placeholder="跨小说/知识库/切片/实体搜索…" class="flex-1" @keyup.enter="doSearch" />
        <Button @click="doSearch">搜索</Button>
      </div>
      <div v-if="results.length" class="space-y-2">
        <div v-for="(r, i) in results" :key="i" class="bg-surface border border-app rounded-[var(--radius-md)] p-3">
          <div class="flex items-center gap-2">
            <Tag :label="r.type" />
            <span class="text-app text-sm font-medium">{{ r.title }}</span>
          </div>
          <p class="text-sm text-muted mt-1">{{ r.snippet }}</p>
        </div>
      </div>
      <EmptyState v-else title="输入关键词开始搜索" desc="支持小说、知识库、切片与实体" />
    </div>

    <!-- 笔记 -->
    <div v-else-if="tab === 'notes'" class="space-y-3">
      <Card>
        <div class="space-y-2">
          <Input v-model="noteForm.title" placeholder="笔记标题（必填）" />
          <textarea
            v-model="noteForm.content"
            rows="3"
            placeholder="笔记内容（必填）"
            class="w-full rounded-[var(--radius-md)] border border-app bg-surface px-3 py-2 text-sm text-app outline-none focus:border-accent"
          ></textarea>
          <div class="flex justify-end">
            <Button :loading="saving" @click="addNote">添加笔记</Button>
          </div>
        </div>
      </Card>
      <div v-for="n in notes" :key="n.id" class="bg-surface border border-app rounded-[var(--radius-md)] p-4">
        <div class="flex items-start justify-between gap-2">
          <div>
            <h3 class="text-app font-medium">{{ n.title }}</h3>
            <p class="text-sm text-muted mt-1">{{ n.content }}</p>
            <p class="text-xs text-muted mt-2">更新于 {{ formatDateTime(n.updated_at) }}</p>
          </div>
          <Button variant="ghost" size="sm" @click="removeNote(n)">删除</Button>
        </div>
      </div>
      <EmptyState v-if="!notes.length" title="还没有笔记" desc="写下你的第一条读书笔记" />
    </div>

    <!-- 标签 -->
    <div v-else-if="tab === 'tags'" class="space-y-3">
      <Card>
        <div class="flex gap-2">
          <Input v-model="tagName" placeholder="标签名（必填）" class="flex-1" @keyup.enter="addTag" />
          <Button :loading="saving" @click="addTag">添加</Button>
        </div>
      </Card>
      <div v-if="tags.length" class="flex flex-wrap gap-2">
        <Tag v-for="t in tags" :key="t" :label="t" />
      </div>
      <EmptyState v-else title="还没有标签" desc="为内容打上标签便于检索" />
    </div>

    <!-- 收藏 -->
    <div v-else-if="tab === 'favorites'" class="space-y-3">
      <Card>
        <div class="space-y-2">
          <Input v-model="favForm.title" placeholder="收藏标题（必填）" />
          <div class="flex gap-2">
            <Input v-model="favForm.target_type" placeholder="类型，如 novel（必填）" class="flex-1" />
            <Input v-model.number="favForm.target_id" type="number" placeholder="对象 id（必填）" class="w-40" />
          </div>
          <div class="flex justify-end">
            <Button :loading="saving" @click="addFavorite">添加收藏</Button>
          </div>
        </div>
      </Card>
      <div
        v-for="f in favorites"
        :key="f.id"
        class="bg-surface border border-app rounded-[var(--radius-md)] p-4 flex items-center justify-between"
      >
        <div>
          <span class="text-app text-sm font-medium">{{ f.title }}</span>
          <span class="text-xs text-muted ml-2">{{ f.type }}</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-muted">{{ f.at }}</span>
          <Button variant="ghost" size="sm" @click="removeFavorite(f)">取消</Button>
        </div>
      </div>
      <EmptyState v-if="!favorites.length" title="还没有收藏" desc="收藏你关注的小说或章节" />
    </div>

    <!-- 审计日志 -->
    <div v-else-if="tab === 'audit'" class="space-y-2">
      <div
        v-for="a in audit"
        :key="a.id"
        class="bg-surface border border-app rounded-[var(--radius-md)] p-3 text-sm flex items-center justify-between"
      >
        <span><span class="text-app">{{ a.action }}</span> · <span class="text-muted">{{ a.target }}</span></span>
        <span class="text-xs text-muted">{{ a.operator }} · {{ a.at }}</span>
      </div>
      <EmptyState v-if="!audit.length" title="暂无操作记录" desc="创建笔记或收藏后会在此留痕" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Tag from '@/components/ui/Tag.vue'
import Card from '@/components/ui/Card.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import {
  globalSearch, listNotes, createNote, deleteNote,
  listTags, createTag,
  listFavorites, createFavorite, deleteFavorite,
  listAuditLogs,
} from '@/api/explore'
import { useToast } from '@/composables/useToast'
import { formatDateTime } from '@/utils/datetime'

// 扩展功能视图（M13）：全局搜索 + 笔记/标签/收藏/审计，所有写操作带成功/失败 toast 提示。
// 整体思路：
//   进入页面并行拉取四类列表；各 tab 提供创建表单，提交后刷新列表并 toast 反馈；
//   后端已做必填校验（空标题/空内容等返回 422），前端捕获 err.message 统一提示。
// 关键点：
//   1. 笔记/收藏支持创建与删除；标签同名自动复用。
//   2. 任何接口异常都通过 notify(err.message, 'error') 反馈，避免静默失败。
const { notify } = useToast()
const tabs = [
  { key: 'search', label: '全局搜索' },
  { key: 'notes', label: '笔记' },
  { key: 'tags', label: '标签' },
  { key: 'favorites', label: '收藏' },
  { key: 'audit', label: '审计日志' }
]
const tab = ref('search')
const search = ref('')
const results = ref([])
const notes = ref([])
const tags = ref([])
const favorites = ref([])
const audit = ref([])
const saving = ref(false)

const noteForm = ref({ title: '', content: '' })
const tagName = ref('')
const favForm = ref({ title: '', target_type: '', target_id: null })

onMounted(async () => {
  try {
    const [n, t, f, a] = await Promise.all([listNotes(), listTags(), listFavorites(), listAuditLogs()])
    notes.value = n.data || []
    tags.value = t.data || []
    favorites.value = f.data || []
    audit.value = a.data || []
  } catch (e) {
    notify(e.message || '加载失败', 'error')
  }
})

async function doSearch() {
  if (!search.value.trim()) {
    notify('请输入搜索关键词', 'info')
    return
  }
  try {
    const res = await globalSearch(search.value.trim())
    results.value = res.data || []
    if (!results.value.length) notify('未找到相关内容', 'info')
  } catch (e) {
    notify(e.message || '搜索失败', 'error')
  }
}

async function addNote() {
  if (!noteForm.value.title.trim() || !noteForm.value.content.trim()) {
    notify('笔记标题与内容均为必填', 'info')
    return
  }
  saving.value = true
  try {
    await createNote({ ...noteForm.value, target_type: 'novel', target_id: null })
    noteForm.value = { title: '', content: '' }
    const res = await listNotes()
    notes.value = res.data || []
    notify('笔记已保存', 'success')
  } catch (e) {
    notify(e.message || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}

async function removeNote(n) {
  try {
    await deleteNote(n.id)
    notes.value = notes.value.filter((x) => x.id !== n.id)
    notify('笔记已删除', 'success')
  } catch (e) {
    notify(e.message || '删除失败', 'error')
  }
}

async function addTag() {
  if (!tagName.value.trim()) {
    notify('标签名必填', 'info')
    return
  }
  saving.value = true
  try {
    await createTag({ name: tagName.value.trim(), color: '' })
    tagName.value = ''
    const res = await listTags()
    tags.value = res.data || []
    notify('标签已添加', 'success')
  } catch (e) {
    notify(e.message || '添加失败', 'error')
  } finally {
    saving.value = false
  }
}

async function addFavorite() {
  if (!favForm.value.title.trim() || !favForm.value.target_type.trim() || !favForm.value.target_id) {
    notify('收藏标题、类型与对象 id 均为必填', 'info')
    return
  }
  saving.value = true
  try {
    await createFavorite({ ...favForm.value, target_id: Number(favForm.value.target_id) })
    favForm.value = { title: '', target_type: '', target_id: null }
    const res = await listFavorites()
    favorites.value = res.data || []
    notify('已收藏', 'success')
  } catch (e) {
    notify(e.message || '收藏失败', 'error')
  } finally {
    saving.value = false
  }
}

async function removeFavorite(f) {
  try {
    await deleteFavorite(f.id)
    favorites.value = favorites.value.filter((x) => x.id !== f.id)
    notify('已取消收藏', 'success')
  } catch (e) {
    notify(e.message || '操作失败', 'error')
  }
}
</script>
