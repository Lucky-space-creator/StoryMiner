<template>
  <div class="space-y-6" v-if="novel">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h2 class="text-xl font-semibold text-app">{{ novel.name }}</h2>
        <p class="text-sm text-muted mt-1">作者：{{ novel.author }}</p>
        <div class="flex flex-wrap gap-1 mt-2">
          <Tag v-for="t in (novel.tags || [])" :key="t" :label="t" />
        </div>
      </div>
      <div class="flex gap-2 shrink-0">
        <input ref="fileEl" type="file" accept=".txt,.epub,.pdf,.docx" class="hidden" @change="onFile" />
        <Button variant="secondary" :loading="uploading" @click="fileEl?.click()">上传文档</Button>
        <Button @click="goKb">建知识库</Button>
      </div>
    </div>

    <p class="text-sm text-app">{{ novel.summary }}</p>

    <Card>
      <div class="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 class="font-medium text-app">章节列表（{{ total }}）</h3>
        <div class="flex items-center gap-2">
          <Input v-model="keyword" placeholder="搜索章节标题" @keyup.enter="onSearch" />
          <Button variant="secondary" size="sm" @click="onSearch">查询</Button>
        </div>
      </div>
      <div v-if="loading" class="space-y-2">
        <Skeleton v-for="i in 5" :key="i" h="2.25rem" />
      </div>
      <Table v-else :columns="columns" :rows="chapters">
        <template #cell-actions="{ row }">
          <Button variant="ghost" size="sm" @click="openChapter(row)">查看</Button>
        </template>
      </Table>
      <Pagination
        v-if="!loading"
        :page="page" :size="size" :total="total"
        @update:page="(p) => { page = p; loadChapters() }"
        @update:size="(s) => { size = s; page = 1; loadChapters() }"
      />
    </Card>

    <!-- 章节查看/编辑抽屉 -->
    <Drawer v-model="drawerOpen" :title="drawerTitle">
      <div v-if="current" class="space-y-4">
        <Input v-model="editForm.title" label="标题" :disabled="!editing" />
        <div>
          <span class="block text-sm text-app mb-1.5">内容</span>
          <textarea
            v-model="editForm.content"
            :disabled="!editing"
            rows="18"
            class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent transition font-mono text-xs"
          ></textarea>
          <span class="text-xs text-muted">字数：{{ editForm.content?.length || 0 }}</span>
        </div>
        <div class="flex justify-end gap-2">
          <Button v-if="!editing" variant="secondary" @click="editing = true">编辑</Button>
          <template v-else>
            <Button variant="ghost" @click="cancelEdit">取消</Button>
            <Button :loading="saving" @click="saveChapter">保存</Button>
          </template>
        </div>
      </div>
      <div v-else class="text-sm text-muted">加载中…</div>
    </Drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Tag from '@/components/ui/Tag.vue'
import Table from '@/components/ui/Table.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Input from '@/components/ui/Input.vue'
import Pagination from '@/components/ui/Pagination.vue'
import { getNovel, listChapters, uploadDocument, getChapter, correctChapter } from '@/api/novels'
import { useSSE } from '@/composables/useSSE'
import { useToast } from '@/composables/useToast'
import { useTaskProgressStore } from '@/stores/taskProgress'

// 小说详情（M1）：加载小说与章节（分页/查询），上传文档经 SSE 跟踪解析进度，
// 章节支持「查看」抽屉内编辑并保存（M1.4 校正）。
// 整体思路：
//   进入页面拉取小说元信息与分页章节；上传触发异步解析，SSE 推送进度；
//   点击「查看」拉取章节正文到抽屉，可切换编辑模式保存。
// 关键点：
//   1. 列表接口已分页，loadChapters 携带 page/size/q。
//   2. 章节正文较大，列表不含 content，查看时单独 GET /chapters/{id}。
//   3. 编辑保存调用 correctChapter，成功后回写 current 并刷新列表。
// 实现逻辑：
//   调 api/novels 真实接口；useSSE 订阅 /parse-tasks/{id}/progress；抽屉内 reactive 表单双向绑定。
const route = useRoute()
const router = useRouter()
const { notify } = useToast()
const task = useTaskProgressStore()
const novel = ref(null)
const chapters = ref([])
const loading = ref(true)
const uploading = ref(false)
const fileEl = ref(null)

// 分页与查询状态
const page = ref(1)
const size = ref(20)
const total = ref(0)
const keyword = ref('')

// 抽屉/编辑状态
const drawerOpen = ref(false)
const current = ref(null)
const editing = ref(false)
const saving = ref(false)
const editForm = reactive({ title: '', content: '' })

const columns = [
  { key: 'chapter_no', label: '序号' },
  { key: 'title', label: '标题' },
  { key: 'word_count', label: '字数' },
  { key: 'actions', label: '操作' }
]

const drawerTitle = computed(() => (editing.value ? '编辑章节' : '查看章节'))

async function loadChapters() {
  loading.value = true
  try {
    const res = await listChapters(route.params.id, {
      page: page.value, size: size.value, q: keyword.value || undefined
    })
    const d = res.data || {}
    chapters.value = d.list || []
    total.value = d.total || 0
    page.value = d.page || page.value
    size.value = d.size || size.value
  } catch (e) {
    notify(e.message || '加载章节失败', 'error')
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  loadChapters()
}

onMounted(async () => {
  try {
    const res = await getNovel(route.params.id)
    novel.value = res.data || null
    await loadChapters()
  } catch (e) {
    notify(e.message || '加载失败', 'error')
  } finally {
    loading.value = false
  }
})

function goKb() {
  router.push('/knowledge-bases')
}

async function onFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const res = await uploadDocument(route.params.id, file)
    const taskId = res.data.task_id
    const name = res.data.name || file.name
    task.upsert({ id: taskId, name, progress: 0, stage: '排队中' })
    task.show()
    startProgress(taskId, name)
    notify('已开始解析，进度见右下角', 'info')
  } catch (err) {
    notify(err.message || '上传失败', 'error')
  } finally {
    uploading.value = false
    if (fileEl.value) fileEl.value.value = ''
  }
}

// 订阅 SSE 解析进度（M1.11）：?token= 携带鉴权。
function startProgress(taskId, name) {
  const token = localStorage.getItem('token') || ''
  const url = `/api/v1/parse-tasks/${taskId}/progress?token=${encodeURIComponent(token)}`
  const { connect, close } = useSSE(url, (ev) => {
    task.upsert({ id: taskId, name, progress: ev.progress || 0, stage: ev.stage || '' })
    if (ev.status === 'success') {
      close()
      loadChapters()
      notify(`《${name}》解析完成`, 'success')
    } else if (ev.status === 'failed') {
      close()
      notify(`解析失败：${ev.payload?.error || '未知错误'}`, 'error')
    }
  })
  connect()
}

// 打开章节查看抽屉：拉取正文并初始化编辑表单。
async function openChapter(row) {
  drawerOpen.value = true
  editing.value = false
  current.value = null
  try {
    const res = await getChapter(row.id)
    current.value = res.data || null
    editForm.title = current.value?.title || ''
    editForm.content = current.value?.content || ''
  } catch (e) {
    notify(e.message || '加载章节失败', 'error')
  }
}

// 取消编辑：回滚表单到查看态。
function cancelEdit() {
  editForm.title = current.value?.title || ''
  editForm.content = current.value?.content || ''
  editing.value = false
}

// 保存章节编辑：调校正接口，成功后回写并刷新列表。
async function saveChapter() {
  if (!current.value) return
  saving.value = true
  try {
    const res = await correctChapter(current.value.id, {
      title: editForm.title, content: editForm.content
    })
    current.value = res.data || current.value
    editForm.title = current.value.title || ''
    editForm.content = current.value.content || ''
    editing.value = false
    notify('章节已保存', 'success')
    loadChapters()
  } catch (e) {
    notify(e.message || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}
</script>
