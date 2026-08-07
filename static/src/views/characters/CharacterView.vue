<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push('/novels')">← 返回小说</button>
        <h2 class="text-lg font-semibold text-app">人物档案</h2>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-sm text-muted">小说</label>
        <select
          v-model="currentNovel"
          @change="load"
          class="bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
        >
          <option v-for="n in novels" :key="n.id" :value="n.id">《{{ n.name }}》</option>
        </select>
        <Button @click="openCreate">新建人物</Button>
        <Button variant="ghost" @click="openAnalyze">按章节分析</Button>
      </div>
    </div>

    <div v-if="loading" class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div v-for="i in 3" :key="i" class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <Skeleton h="1.25rem" w="50%" />
        <Skeleton h="0.875rem" w="70%" class="mt-3" />
      </div>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div
        v-for="c in characters"
        :key="c.id"
        @click="open(c)"
        class="bg-surface border border-app rounded-[var(--radius-md)] p-5 hover:border-accent transition cursor-pointer relative group"
      >
        <div class="absolute top-3 right-3 flex items-center gap-1 z-10">
          <button
            class="opacity-0 group-hover:opacity-100 text-muted hover:text-accent transition"
            @click.stop="openEdit(c)"
            title="编辑人物"
          ><PhPencilSimple :size="15" /></button>
          <button
            class="opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition"
            @click.stop="askDelete(c)"
            title="删除人物"
          ><PhTrash :size="15" /></button>
        </div>
        <div class="flex items-center justify-between">
          <h3 class="font-medium text-app">{{ c.name }}</h3>
          <Tag :label="c.role" />
        </div>
        <p class="text-sm text-muted mt-2 line-clamp-2">{{ c.desc }}</p>
        <p class="text-xs text-muted mt-3">出场 {{ c.appearances }} 次</p>
      </div>
      <div
        v-if="!loading && characters.length === 0"
        class="col-span-full bg-surface border border-dashed border-app rounded-[var(--radius-md)] p-8 text-center text-sm text-muted"
      >该小说暂无人物，点击「新建人物」添加。</div>
    </div>

    <Drawer v-model="openDetail" :title="current?.name || '人物'">
      <div v-if="current" class="space-y-4">
        <!-- 内联 tab 切换：角色身份 / 角色形象 / 角色经历 / 关键事件 -->
        <div class="flex border-b border-app/20 -mx-2 px-2">
          <button v-for="t in tabs" :key="t.key" @click="tab = t.key"
            :class="['px-3 py-2 text-sm transition border-b-2 -mb-[1px]',
              tab === t.key ? 'border-accent text-accent font-medium' : 'border-transparent text-muted hover:text-app']">
            {{ t.label }}
          </button>
        </div>

        <!-- 1. 角色身份 -->
        <div v-if="tab === 'identity'" class="space-y-3">
          <div class="flex flex-wrap items-center gap-2">
            <Tag :label="current.role || '未知'" />
            <span v-if="current.gender" class="px-2 py-0.5 bg-surface rounded border border-app/20 text-xs text-muted">{{ current.gender }}</span>
            <span class="text-sm text-muted">出场 {{ current.appearances || 0 }} 次</span>
          </div>
          <div v-if="current.identity || current.personality" class="bg-surface rounded-lg p-3 space-y-2 border border-app/10">
            <div v-if="current.identity" class="text-sm">
              <span class="text-muted">身份：</span><span class="text-app">{{ current.identity }}</span>
            </div>
            <div v-if="current.personality" class="text-sm">
              <span class="text-muted">性格：</span><span class="text-app">{{ current.personality }}</span>
            </div>
          </div>
          <p v-if="current.catchphrase" class="text-sm text-app italic px-3">「{{ current.catchphrase }}」</p>
          <p v-if="!current.identity && !current.personality && !current.catchphrase" class="text-sm text-muted italic">暂无更多身份信息</p>
        </div>

        <!-- 2. 角色形象 -->
        <div v-else-if="tab === 'appearance'" class="space-y-3">
          <p v-if="current.appearance" class="text-sm text-app leading-relaxed whitespace-pre-wrap">{{ current.appearance }}</p>
          <p v-else class="text-sm text-muted italic">暂无外貌描述（书中未明确描绘或尚未分析）</p>
        </div>

        <!-- 3. 角色经历 -->
        <div v-else-if="tab === 'experience'" class="space-y-3">
          <p v-if="current.desc" class="text-sm text-app leading-relaxed whitespace-pre-wrap">{{ current.desc }}</p>
          <p v-else class="text-sm text-muted italic">暂无经历记录，可点击下方「AI 生成小传」</p>
        </div>

        <!-- 4. 关键事件 -->
        <div v-else-if="tab === 'events'" class="space-y-3">
          <template v-if="current.key_events?.length">
            <div v-for="(ev, i) in current.key_events" :key="i" class="bg-surface rounded-lg p-3 border border-app/10">
              <p class="text-sm text-app leading-relaxed">{{ ev.event }}</p>
              <p class="text-xs text-muted mt-1">{{ ev.chapters }}</p>
            </div>
          </template>
          <p v-else class="text-sm text-muted italic">暂无关键事件记录，重新执行人物分析后可自动提取</p>
        </div>

        <div class="flex justify-end gap-2 pt-2 border-t border-app/10">
          <Button variant="ghost" @click="openEdit(current)">编辑</Button>
          <Button variant="ghost" @click="generate">AI 生成小传</Button>
          <Button @click="openDetail = false">关闭</Button>
        </div>
      </div>
    </Drawer>

    <!-- 编辑人物抽屉 -->
    <Drawer v-model="editOpen" :title="`编辑人物 · ${editForm.name}`">
      <div v-if="editing" class="space-y-4">
        <Input v-model="editForm.name" label="姓名" disabled />
        <div>
          <label class="block text-sm text-app mb-1">身份</label>
          <select v-model="editForm.role" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="主角">主角</option>
            <option value="配角">配角</option>
            <option value="反派">反派</option>
            <option value="势力">势力</option>
          </select>
        </div>
        <Input v-model="editForm.gender" label="性别" placeholder="男 / 女 / 未知" />
        <Input v-model="editForm.identity" label="身份/职业" placeholder="如：剑客" />
        <Input v-model="editForm.personality" label="性格" placeholder="如：沉稳内敛" />
        <Input v-model="editForm.appearance" label="外貌" placeholder="外貌描写" />
        <Input v-model="editForm.catchphrase" label="口头禅" placeholder="如：天命所归" />
        <Input v-model="editForm.desc" label="简介/小传" placeholder="人物设定、经历…" />
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="editOpen = false">取消</Button>
          <Button type="button" :loading="saving" @click="saveEdit">保存</Button>
        </div>
      </div>
    </Drawer>

    <!-- 按章节分析人物 -->
    <Modal v-model="analyzeOpen" title="按章节分析人物">
      <div v-if="analyzeChaptersList.length" class="space-y-3">
        <p class="text-sm text-muted">勾选要分析的章节（仅对该部分正文抽取人物并写入人物档案）：</p>
        <div class="max-h-72 overflow-auto space-y-1 border border-app/10 rounded-lg p-2">
          <label
            v-for="ch in analyzeChaptersList"
            :key="ch.id"
            class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-surface cursor-pointer text-sm"
          >
            <input type="checkbox" :value="ch.id" v-model="selectedChapters" class="accent-accent" />
            <span class="text-app">第 {{ ch.chapter_no }} 章 · {{ ch.title || '（无标题）' }}</span>
          </label>
        </div>
        <p v-if="selectedChapters.length === 0" class="text-xs text-danger">请至少选择 1 个章节</p>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="analyzeOpen = false">取消</Button>
          <Button type="button" :loading="analyzing" @click="doAnalyzeChapters">开始分析</Button>
        </div>
      </div>
      <div v-else class="text-sm text-muted text-center py-6">该小说暂无章节，请先上传并解析文档。</div>
    </Modal>

    <Modal v-model="createOpen" title="新建人物">
      <form @submit.prevent="add" class="space-y-4">
        <Input v-model="form.name" label="姓名" placeholder="如：陈平安" />
        <div>
          <label class="block text-sm text-app mb-1">身份</label>
          <select v-model="form.role" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="主角">主角</option>
            <option value="配角">配角</option>
            <option value="反派">反派</option>
            <option value="势力">势力</option>
          </select>
        </div>
        <Input v-model="form.desc" label="简介" placeholder="人物设定、性格、经历…" />
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="createOpen = false">取消</Button>
          <Button type="submit" :loading="adding">创建</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除人物" :message="`确定删除人物「${pending?.name}」？`" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { PhTrash, PhPencilSimple } from '@phosphor-icons/vue'
import Tag from '@/components/ui/Tag.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { listCharacters, getCharacter, createCharacter, deleteCharacter, generateCharacter, updateCharacter, analyzeChapters, listAllChapters } from '@/api/characters'
import { listNovels } from '@/api/novels'
import { useToast } from '@/composables/useToast'
import { useTaskProgressStore } from '@/stores/taskProgress'

const { notify } = useToast()
const taskStore = useTaskProgressStore()
const novels = ref([])
const characters = ref([])
const loading = ref(true)
const currentNovel = ref(Number(useRoute().params.id) || null)
const openDetail = ref(false)
const current = ref(null)
const createOpen = ref(false)
const adding = ref(false)
const form = ref({ name: '', role: '主角', desc: '' })
const delOpen = ref(false)
const pending = ref(null)
const myTaskId = ref(null)

// 编辑人物（Feature 2）
const editOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const editForm = ref({ name: '', role: '配角', gender: '', identity: '', personality: '', appearance: '', catchphrase: '', desc: '' })

// 按章节分析人物（Feature 1）
const analyzeOpen = ref(false)
const analyzing = ref(false)
const analyzeChaptersList = ref([])
const selectedChapters = ref([])

// 人物详情抽屉 4 区块 tab
const tab = ref('identity')
const tabs = [
  { key: 'identity', label: '角色身份' },
  { key: 'appearance', label: '角色形象' },
  { key: 'experience', label: '角色经历' },
  { key: 'events', label: '关键事件' },
]

onMounted(async () => {
  const res = await listNovels()
  novels.value = res.data?.list || []
  if (!currentNovel.value && novels.value.length) currentNovel.value = novels.value[0].id
  await load()
})

async function load() {
  if (!currentNovel.value) return
  loading.value = true
  const res = await listCharacters(currentNovel.value)
  characters.value = res.data || []
  loading.value = false
}

async function open(c) {
  const res = await getCharacter(c.id)
  current.value = res.data || c
  tab.value = 'identity'
  openDetail.value = true
}

async function generate() {
  // 实现逻辑：触发后台生成小传，立即弹窗提示「正在后台处理中」；全局轮询刷新进度，
  // 本页 watcher 在任务完成时刷新当前人物卡（若抽屉打开）。
  if (!current.value) return
  try {
    const res = await generateCharacter(current.value.id)
    const taskId = res.data?.task_id
    if (!taskId) {
      const r = await getCharacter(current.value.id)
      current.value = r.data || current.value
      notify('已生成人物小传', 'success')
      return
    }
    myTaskId.value = taskId
    taskStore.upsert({ id: taskId, type: 'character', name: `小说${novels.value.find(n => n.id === currentNovel.value)?.name || '未知'}-人物抽取实体`, progress: 0, stage: '已提交，后台处理中', status: 'running' })
    taskStore.show()
    notify('已提交，正在后台处理中…', 'info')
  } catch (e) {
    notify(e?.message || '生成失败', 'error')
  }
}

// 监听本页生成任务完成：由全局轮询更新 store，这里刷新当前人物卡或列表
watch(
  () => taskStore.tasks.find((t) => t.id === myTaskId.value)?.status,
  async (s) => {
    if (s === 'success' && myTaskId.value) {
      myTaskId.value = null
      // 章节分析任务完成后刷新列表（也会更新 current 详情）
      await load()
      if (current.value) {
        const r = await getCharacter(current.value.id)
        current.value = r.data || current.value
      }
      notify('人物分析完成', 'success')
    } else if (s === 'failed') {
      myTaskId.value = null
    }
  }
)

function openCreate() {
  form.value = { name: '', role: '主角', desc: '' }
  createOpen.value = true
}

async function add() {
  if (!currentNovel.value) return
  adding.value = true
  const res = await createCharacter(currentNovel.value, { ...form.value })
  if (res.code === 0) {
    characters.value.push(res.data)
    createOpen.value = false
    notify('人物创建成功', 'success')
  }
  adding.value = false
}

function askDelete(c) {
  pending.value = c
  delOpen.value = true
}

async function doDelete() {
  if (!pending.value) return
  await deleteCharacter(pending.value.id)
  characters.value = characters.value.filter((x) => x.id !== pending.value.id)
  notify(`已删除人物「${pending.value.name}」`, 'success')
  pending.value = null
}

// ── Feature 2：编辑单个人物档案 ──
function openEdit(c) {
  editForm.value = {
    name: c.name,
    role: c.role || '配角',
    gender: c.gender || '',
    identity: c.identity || '',
    personality: c.personality || '',
    appearance: c.appearance || '',
    catchphrase: c.catchphrase || '',
    desc: c.desc || '',
  }
  editing.value = c
  editOpen.value = true
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  const payload = {
    role: editForm.value.role,
    gender: editForm.value.gender || null,
    identity: editForm.value.identity || null,
    personality: editForm.value.personality || null,
    appearance: editForm.value.appearance || null,
    catchphrase: editForm.value.catchphrase || null,
    desc: editForm.value.desc || null,
  }
  const res = await updateCharacter(editing.value.id, payload)
  if (res.code === 0) {
    // 同步更新列表与详情
    const idx = characters.value.findIndex((x) => x.id === editing.value.id)
    if (idx !== -1) characters.value[idx] = { ...characters.value[idx], role: res.data.role, desc: res.data.desc }
    if (current.value && current.value.id === editing.value.id) current.value = res.data
    editOpen.value = false
    notify('人物档案已更新', 'success')
  }
  saving.value = false
}

// ── Feature 1：按章节分析人物 ──
async function openAnalyze() {
  selectedChapters.value = []
  // 拉取全部章节（后端单页上限 100，用聚合函数适配长篇小说）
  analyzeChaptersList.value = await listAllChapters(currentNovel.value)
  analyzeOpen.value = true
}

async function doAnalyzeChapters() {
  if (selectedChapters.value.length === 0) {
    notify('请至少选择 1 个章节', 'error')
    return
  }
  analyzing.value = true
  try {
    const res = await analyzeChapters(currentNovel.value, selectedChapters.value)
    const taskId = res.data?.task_id
    if (taskId) {
      const novelName = novels.value.find((n) => n.id === currentNovel.value)?.name || '未知'
      taskStore.upsert({ id: taskId, type: 'character', name: `小说${novelName}-章节人物分析`, progress: 0, stage: '已提交，后台处理中', status: 'running' })
      taskStore.show()
      notify('已提交，正在后台分析…', 'info')
    }
    analyzeOpen.value = false
    // 任务完成后由下方 watcher 刷新列表
    myTaskId.value = taskId
  } catch (e) {
    notify(e?.message || '分析失败', 'error')
  }
  analyzing.value = false
}
</script>
