<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">我的小说</h2>
      <Button @click="openUpload">上传小说</Button>
    </div>

    <div v-if="loading" class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div v-for="i in 3" :key="i" class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <Skeleton h="1.25rem" w="60%" />
        <Skeleton h="0.875rem" w="40%" class="mt-3" />
        <Skeleton h="0.75rem" w="50%" class="mt-4" />
      </div>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div
        v-for="n in novels"
        :key="n.id"
        class="bg-surface border border-app rounded-[var(--radius-md)] p-5 hover:border-accent transition cursor-pointer relative group"
        @click="$router.push(`/novels/${n.id}`)"
      >
        <button
          class="absolute top-3 right-3 opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition z-10"
          @click.stop="askDelete(n)"
          title="删除小说"
        ><PhTrash :size="16" /></button>
        <div class="w-full h-28 rounded-[var(--radius-sm)] bg-surface2 mb-3 overflow-hidden flex items-center justify-center">
          <img v-if="n.cover" :src="n.cover" class="w-full h-full object-cover" alt="封面" />
          <PhBookOpen v-else :size="32" class="text-muted" />
        </div>
        <h3 class="font-medium text-app truncate">{{ n.name }}</h3>
        <p class="text-sm text-muted mt-1">作者：{{ n.author }}</p>
        <div class="flex flex-wrap gap-1 mt-3">
          <Tag v-for="t in n.tags" :key="t" :label="t" />
        </div>
        <p class="text-xs text-muted mt-3">{{ n.chapter_count }} 章 · {{ statusText(n.status) }}</p>
      </div>
    </div>

    <Modal v-model="showUpload" title="上传小说">
      <form @submit.prevent="submitUpload" class="space-y-4">
        <div>
          <label class="block text-sm text-app mb-1.5">封面（可选）</label>
          <div class="flex items-center gap-3">
            <div class="w-20 h-24 rounded-[var(--radius-sm)] bg-surface2 overflow-hidden flex items-center justify-center shrink-0">
              <img v-if="form.cover" :src="form.cover" class="w-full h-full object-cover" alt="封面" />
              <PhBookOpen v-else :size="22" class="text-muted" />
            </div>
            <div class="flex flex-col gap-2">
              <input ref="fileEl" type="file" accept="image/*" class="hidden" @change="onCover" />
              <Button variant="secondary" size="sm" type="button" @click="fileEl?.click()">选择图片</Button>
              <Button v-if="form.cover" variant="ghost" size="sm" type="button" @click="form.cover = ''">移除</Button>
            </div>
          </div>
        </div>
        <Input v-model="form.name" label="书名" placeholder="请输入小说名称" />
        <Input v-model="form.author" label="作者" placeholder="可选" />
        <Input v-model="form.summary" label="简介" placeholder="可选" />

        <div>
          <label class="block text-sm text-app mb-1.5">标签</label>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="t in defaultTags"
              :key="t"
              type="button"
              @click="toggleTag(t)"
              class="px-2.5 py-1 rounded-full text-xs border transition"
              :class="form.tags.includes(t) ? 'border-accent text-accent bg-surface2' : 'border-app text-muted hover:border-accent'"
            >{{ t }}</button>
          </div>
          <div class="flex gap-2 mt-2">
            <Input v-model="customTag" placeholder="自定义标签，回车添加" class="flex-1" @keyup.enter="addCustomTag" />
            <Button variant="secondary" type="button" @click="addCustomTag">添加</Button>
          </div>
          <div v-if="form.tags.length" class="flex flex-wrap gap-1.5 mt-2">
            <span v-for="t in form.tags" :key="t" class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-surface2 text-app">
              {{ t }}
              <button type="button" class="text-muted hover:text-danger" @click="toggleTag(t)"><PhX :size="11" /></button>
            </span>
          </div>
        </div>

        <div>
          <label class="block text-sm text-app mb-1.5">上传文档（可选，可多选）</label>
          <input ref="docsEl" type="file" accept=".txt,.epub,.pdf,.docx" multiple class="hidden" @change="onDocs" />
          <div class="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" type="button" @click="docsEl?.click()">选择文件</Button>
            <span
              v-for="f in form.files"
              :key="f.name"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-surface2 text-app"
            >{{ f.name }}</span>
          </div>
        </div>

        <p class="text-xs text-muted">创建后文档将自动解析切章；进入详情页可继续上传或构建知识库索引。</p>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="showUpload = false">取消</Button>
          <Button type="submit" :loading="uploading">创建</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除小说" :message="`确定删除《${pending?.name}》？该小说的知识库、文档、切片、人物、图谱将全部清除，且不可恢复。`" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { PhTrash, PhX, PhBookOpen } from '@phosphor-icons/vue'
import Button from '@/components/ui/Button.vue'
import Tag from '@/components/ui/Tag.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Modal from '@/components/ui/Modal.vue'
import Input from '@/components/ui/Input.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { listNovels, createNovel, deleteNovel, uploadDocuments } from '@/api/novels'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const novels = ref([])
const loading = ref(true)
const showUpload = ref(false)
const uploading = ref(false)
const defaultTags = ref(['仙侠', '江湖', '克苏鲁', '蒸汽朋克', '修仙', '凡人流', '玄幻', '都市', '科幻', '悬疑'])
const customTag = ref('')
const fileEl = ref(null)
const docsEl = ref(null)
const form = ref({ name: '', author: '', summary: '', cover: '', tags: [], files: [] })

const delOpen = ref(false)
const pending = ref(null)

const statusMap = { done: '已完成', parsing: '解析中', pending: '待解析', failed: '失败' }
function statusText(s) { return statusMap[s] || s }

function openUpload() {
  form.value = { name: '', author: '', summary: '', cover: '', tags: [], files: [] }
  showUpload.value = true
}

function onDocs(e) {
  const files = Array.from(e.target.files || [])
  form.value.files = files
  if (docsEl.value) docsEl.value.value = ''
}

function onCover(e) {
  const file = e.target.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => (form.value.cover = reader.result)
  reader.readAsDataURL(file)
}

function toggleTag(t) {
  const arr = form.value.tags
  const i = arr.indexOf(t)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(t)
}

function addCustomTag() {
  const t = customTag.value.trim()
  if (t && !form.value.tags.includes(t)) form.value.tags.push(t)
  customTag.value = ''
}

function askDelete(n) {
  pending.value = n
  delOpen.value = true
}

async function doDelete() {
  if (!pending.value) return
  try {
    await deleteNovel(pending.value.id)
    novels.value = novels.value.filter((x) => x.id !== pending.value.id)
    notify(`已删除《${pending.value.name}》及其全部关联数据`, 'success')
  } catch (e) {
    notify(e.message || '删除失败', 'error')
  } finally {
    pending.value = null
  }
}

onMounted(async () => {
  try {
    const res = await listNovels()
    novels.value = res.data?.list || []
  } catch (e) {
    notify(e.message || '加载小说失败', 'error')
  } finally {
    loading.value = false
  }
})

async function submitUpload() {
  uploading.value = true
  try {
    const res = await createNovel({
      name: form.value.name,
      author: form.value.author,
      summary: form.value.summary,
      cover: form.value.cover,
      tags: form.value.tags,
    })
    const novelId = res.data.id
    if (form.value.files && form.value.files.length) {
      await uploadDocuments(novelId, form.value.files)
    }
    novels.value.unshift(res.data)
    showUpload.value = false
    notify('小说创建成功' + (form.value.files.length ? '，文档解析中' : ''), 'success')
  } catch (e) {
    notify(e.message || '创建失败', 'error')
  } finally {
    uploading.value = false
  }
}
</script>
