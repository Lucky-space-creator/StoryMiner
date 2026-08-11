<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push(`/novels/${novelId}`)">← 返回小说</button>
        <h2 class="text-lg font-semibold text-app">章节漫剧列</h2>
        <p class="text-xs text-muted mt-1">
          当前小说：<span class="text-app font-medium">{{ novelName || '加载中…' }}</span>
          （共 {{ chapterOptions.length }} 章）· 逐章制作漫剧片段，每次选择一章，自动解析出场角色，作为 AI 漫剧生产素材。
        </p>
      </div>
      <Button @click="openCreate" :disabled="!chapterOptions.length" :title="chapterOptions.length ? '' : '该小说暂无章节，无法创建'">新建漫剧片段</Button>
    </div>

    <div v-if="loading" class="space-y-3">
      <div v-for="i in 3" :key="i" class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <Skeleton h="1.25rem" w="50%" />
      </div>
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="d in dramas"
        :key="d.id"
        class="bg-surface border border-app rounded-[var(--radius-md)] p-5 relative group cursor-pointer hover:border-accent transition"
        @click="goDetail(d)"
      >
        <div class="absolute top-3 right-3 flex items-center gap-2 opacity-0 group-hover:opacity-100 transition">
          <button
            class="text-muted hover:text-accent"
            @click.stop="goDetail(d)"
            title="查看详情 / 生成漫剧场景"
          ><PhFilmStrip :size="15" /></button>
          <button
            class="text-muted hover:text-danger"
            @click.stop="askDelete(d)"
            title="删除"
          ><PhTrash :size="15" /></button>
        </div>
        <div class="flex items-center justify-between pr-12">
          <h3 class="font-medium text-app">{{ d.title }}</h3>
          <span class="text-xs text-muted">第 {{ d.chapter_from }} 章</span>
        </div>
        <div v-if="d.characters?.length" class="flex flex-wrap gap-2 mt-3">
          <span
            v-for="c in d.characters"
            :key="c.id"
            class="px-2 py-0.5 rounded-full bg-surface border border-app/20 text-xs text-app"
          >{{ c.name }}<span v-if="c.role" class="text-muted"> · {{ c.role }}</span></span>
        </div>
        <p v-else class="text-xs text-muted mt-3">（无出场角色匹配）</p>
        <p v-if="d.summary" class="text-sm text-muted mt-3 leading-relaxed">{{ d.summary }}</p>
        <p v-if="d.scene" class="text-xs text-accent mt-2">✓ 已生成漫剧场景</p>
      </div>
      <div
        v-if="!loading && dramas.length === 0"
        class="bg-surface border border-dashed border-app rounded-[var(--radius-md)] p-8 text-center text-sm text-muted"
      >暂无章节漫剧，点击「新建漫剧片段」开始。</div>
    </div>

    <Modal v-model="createOpen" title="新建章节漫剧片段">
      <form @submit.prevent="add" class="space-y-4">
        <p class="text-xs text-muted">为小说《{{ novelName || '当前小说' }}》选择一章生成单集漫剧素材。</p>
        <div>
          <label class="block text-sm text-app mb-1">选择章节</label>
          <select v-model.number="form.chapter_no" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option v-for="ch in chapterOptions" :key="'c'+ch.chapter_no" :value="ch.chapter_no">第 {{ ch.chapter_no }} 章 · {{ ch.title || '（无标题）' }}</option>
          </select>
        </div>
        <p class="text-xs text-muted">漫剧片段按单章制作，每次仅取一章；摘要由系统根据所选章节与出场角色自动生成。</p>
        <Input v-model="form.title" label="标题（可选）" placeholder="不填则自动生成「第x章」" />
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="createOpen = false">取消</Button>
          <Button type="submit" :loading="adding">创建</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除章节漫剧" :message="`确定删除「${pending?.title}」？`" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PhTrash, PhFilmStrip } from '@phosphor-icons/vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { listChapterDramas, createChapterDrama, deleteChapterDrama } from '@/api/chapterDramas'
import { listAllChapters } from '@/api/characters'
import { getNovel } from '@/api/novels'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const route = useRoute()
const router = useRouter()
const novelId = Number(route.params.id) || null

const dramas = ref([])
const loading = ref(true)
const createOpen = ref(false)
const adding = ref(false)
const delOpen = ref(false)
const pending = ref(null)
const chapterOptions = ref([])
const novelName = ref('')
const form = ref({ chapter_no: null, title: '' })

onMounted(load)

async function load() {
  if (!novelId) return
  loading.value = true
  try {
    const res = await getNovel(novelId)
    novelName.value = res.data?.name || ''
  } catch (e) {
    novelName.value = ''
  }
  const [dramaRes, chapters] = await Promise.all([
    listChapterDramas(novelId),
    listAllChapters(novelId),
  ])
  dramas.value = dramaRes.data || []
  chapterOptions.value = chapters
  loading.value = false
}

function openCreate() {
  // 章节为空时不打开弹窗，从源头避免提交 null 触发 422 参数校验失败
  const opts = chapterOptions.value
  if (!opts.length) {
    notify('该小说暂无章节，无法创建章节漫剧', 'error')
    return
  }
  // 默认选第一/最后一章，单章制作
  const first = opts[0]?.chapter_no
  form.value = { chapter_no: first ?? null, title: '' }
  createOpen.value = true
}

async function add() {
  if (form.value.chapter_no == null) {
    notify('请选择章节', 'error')
    return
  }
  adding.value = true
  const no = Number(form.value.chapter_no)
  // 单章制作：起始与结束均为所选章节
  const res = await createChapterDrama(novelId, {
    chapter_from: no,
    chapter_to: no,
    title: form.value.title || null,
  })
  if (res.code === 0) {
    dramas.value.push(res.data)
    createOpen.value = false
    notify('章节漫剧已创建', 'success')
  }
  adding.value = false
}

function askDelete(d) {
  pending.value = d
  delOpen.value = true
}

function goDetail(d) {
  // 进入章节漫剧详情页：展示/生成/保存导演场景分析
  router.push(`/novels/${novelId}/chapter-dramas/${d.id}`)
}

async function doDelete() {
  if (!pending.value) return
  await deleteChapterDrama(novelId, pending.value.id)
  dramas.value = dramas.value.filter((x) => x.id !== pending.value.id)
  notify(`已删除「${pending.value.title}」`, 'success')
  pending.value = null
}
</script>
