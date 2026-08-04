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
        <button
          class="absolute top-3 right-3 opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition z-10"
          @click.stop="askDelete(c)"
          title="删除人物"
        ><PhTrash :size="15" /></button>
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
          <Button variant="ghost" @click="generate">AI 生成小传</Button>
          <Button @click="openDetail = false">关闭</Button>
        </div>
      </div>
    </Drawer>

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
import { PhTrash } from '@phosphor-icons/vue'
import Tag from '@/components/ui/Tag.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { listCharacters, getCharacter, createCharacter, deleteCharacter, generateCharacter } from '@/api/characters'
import { listNovels } from '@/api/novels'
import { getTask } from '@/api/tasks'
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

// 监听本页生成任务完成：由全局轮询更新 store，这里刷新当前人物卡
watch(
  () => taskStore.tasks.find((t) => t.id === myTaskId.value)?.status,
  async (s) => {
    if (s === 'success' && myTaskId.value && current.value) {
      myTaskId.value = null
      const r = await getCharacter(current.value.id)
      current.value = r.data || current.value
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
</script>
