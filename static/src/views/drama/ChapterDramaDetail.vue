<template>
  <div class="space-y-6" v-if="drama">
    <!-- 头部：返回 + 标题 + 操作 -->
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push(`/novels/${novelId}/chapter-dramas`)">← 返回章节漫剧列</button>
        <h2 class="text-lg font-semibold text-app">{{ drama.title }}</h2>
        <p class="text-xs text-muted mt-1">
          当前小说：<span class="text-app font-medium">{{ novelName || '加载中…' }}</span>
          · 第 {{ drama.chapter_from }} 章（单章漫剧片段）
        </p>
      </div>
      <div class="flex gap-2">
        <Button
          variant="secondary"
          :loading="generating || polling"
          :disabled="generating || polling"
          @click="generate"
        >
          {{ polling ? '生成任务进行中…' : '生成漫剧场景' }}
        </Button>
        <Button :disabled="!canSave || generating || polling" :loading="saving" @click="save">保存</Button>
      </div>
    </div>

    <!-- 生成进度条：防重复点击期间 / 刷新后识别到任务进行中 时展示 -->
    <div
      v-if="generating || polling"
      class="bg-surface border border-accent/30 rounded-[var(--radius-md)] p-4"
    >
      <div class="flex items-center gap-2 mb-2">
        <span class="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin"></span>
        <span class="text-sm text-app font-medium">
          {{ generating ? '导演 Agent 正在生成漫剧场景…' : '检测到生成任务进行中，请勿重复操作' }}
        </span>
      </div>
      <div class="h-1.5 w-full bg-surface2 rounded-full overflow-hidden">
        <div class="h-full bg-accent indeterminate-bar"></div>
      </div>
      <p class="text-xs text-muted mt-2">预计需要 1–2 分钟（本地模型逐段生成），完成后会自动刷新结果。</p>
    </div>

    <!-- 出场角色 -->
    <div v-if="drama.characters?.length" class="flex flex-wrap gap-2">
      <span v-for="c in drama.characters" :key="c.id"
        class="px-2 py-0.5 rounded-full bg-surface border border-app/20 text-xs text-app">
        {{ c.name }}<span v-if="c.role" class="text-muted"> · {{ c.role }}</span>
      </span>
    </div>
    <p v-if="drama.summary" class="text-sm text-muted leading-relaxed">{{ drama.summary }}</p>

    <!-- 上集回顾：导演 Agent 基于上一章正文生成 100–200 字衔接摘要 -->
    <section v-if="scene?.prev_chapter_review" class="bg-surface2/60 border border-app/15 rounded-[var(--radius-md)] p-4">
      <h3 class="flex items-center gap-2 font-medium text-app mb-2">
        <span class="w-6 h-6 rounded-full bg-accent/10 text-accent text-xs flex items-center justify-center font-semibold">↑</span>
        上集回顾
      </h3>
      <p class="text-sm text-app leading-relaxed whitespace-pre-wrap">{{ scene.prev_chapter_review }}</p>
    </section>

    <!-- 四段式分析：场景设计 / 剧情安排 / 镜头运转 / 预计时长 -->
    <div v-if="scene" class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- 场景设计 -->
      <section class="bg-surface border border-app rounded-[var(--radius-md)] p-5 flex flex-col">
        <h3 class="flex items-center gap-2 font-medium text-app mb-3">
          <span class="w-6 h-6 rounded-full bg-accent/10 text-accent text-xs flex items-center justify-center font-semibold">1</span>
          场景设计
        </h3>
        <ol v-if="scene.scene_design?.length" class="list-decimal list-inside space-y-2 text-sm text-app leading-relaxed marker:text-accent/60">
          <li v-for="(it, i) in scene.scene_design" :key="i" class="pl-1">{{ it }}</li>
        </ol>
        <p v-else class="text-xs text-muted">暂无场景设计</p>
      </section>

      <!-- 镜头运转 -->
      <section class="bg-surface border border-app rounded-[var(--radius-md)] p-5 flex flex-col">
        <h3 class="flex items-center gap-2 font-medium text-app mb-3">
          <span class="w-6 h-6 rounded-full bg-accent/10 text-accent text-xs flex items-center justify-center font-semibold">3</span>
          镜头运转
        </h3>
        <ol v-if="scene.camera_movement?.length" class="list-decimal list-inside space-y-2 text-sm text-app leading-relaxed marker:text-accent/60">
          <li v-for="(it, i) in scene.camera_movement" :key="i" class="pl-1">{{ it }}</li>
        </ol>
        <p v-else class="text-xs text-muted">暂无镜头运转</p>
      </section>

      <!-- 剧情安排（占整宽，内容最长） -->
      <section class="bg-surface border border-app rounded-[var(--radius-md)] p-5 lg:col-span-2 flex flex-col">
        <h3 class="flex items-center gap-2 font-medium text-app mb-3">
          <span class="w-6 h-6 rounded-full bg-accent/10 text-accent text-xs flex items-center justify-center font-semibold">2</span>
          剧情安排
        </h3>
        <div v-if="scene.plot_arrangement?.length" class="space-y-3">
          <div
            v-for="(it, i) in scene.plot_arrangement"
            :key="i"
            class="flex gap-3 pl-3 py-2 border-l-2 border-accent/40 bg-surface2/40 rounded-r-md"
          >
            <span class="text-xs font-semibold text-accent/70 mt-0.5 shrink-0">第{{ i + 1 }}幕</span>
            <p class="text-sm text-app leading-relaxed whitespace-pre-wrap">{{ it }}</p>
          </div>
        </div>
        <p v-else class="text-xs text-muted">暂无剧情安排</p>
      </section>

      <!-- 预计时长 -->
      <section class="bg-gradient-to-br from-accent/5 to-transparent border border-accent/30 rounded-[var(--radius-md)] p-5 lg:col-span-2 flex items-center justify-between">
        <div>
          <h3 class="flex items-center gap-2 font-medium text-app mb-1">
            <span class="w-6 h-6 rounded-full bg-accent/10 text-accent text-xs flex items-center justify-center font-semibold">4</span>
            预计时长
          </h3>
          <p class="text-sm text-app leading-relaxed">{{ scene.duration_estimate || '（未生成）' }}</p>
        </div>
      </section>
    </div>
    <div v-else class="bg-surface border border-dashed border-app rounded-[var(--radius-md)] p-10 text-center text-sm text-muted">
      点击右上角「生成漫剧场景」，由导演 Agent 基于章节正文与出场角色生成分镜分析。
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import Button from '@/components/ui/Button.vue'
import { getChapterDrama, generateDirectorScene, saveChapterDramaScene, getDirectorStatus } from '@/api/chapterDramas'
import { getNovel } from '@/api/novels'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const route = useRoute()
const novelId = Number(route.params.id) || null
const dramaId = Number(route.params.dramaId) || null

const drama = ref(null)
const scene = ref(null)
const novelName = ref('')
const generating = ref(false)   // 本轮请求进行中
const saving = ref(false)
const polling = ref(false)      // 刷新后识别到后端生成任务进行中，正在轮询
let pollTimer = null

// 草稿（生成但未保存）与已保存共享 scene；canSave 表示当前有可保存的草稿
const hasDraft = ref(false)
const canSave = computed(() => hasDraft.value && !!scene.value)
// 按钮置灰条件：本轮生成中 或 后端存在进行中任务（刷新后由 drama.generating 提供）
const disabled = computed(() => generating.value || polling.value || !!drama.value?.generating)

async function load() {
  try {
    const res = await getNovel(novelId)
    novelName.value = res.data?.name || ''
  } catch (e) { novelName.value = '' }
  const res = await getChapterDrama(novelId, dramaId)
  drama.value = res.data
  // 进入时若已保存过 scene，直接展示
  if (res.data?.scene) {
    scene.value = res.data.scene
    hasDraft.value = true
  }
  // 刷新后若后端标记生成中，进入轮询恢复模式（置灰 + 进度条）
  if (res.data?.generating) {
    startPolling()
  }
}

// 轮询后端生成状态：变 False 时说明本轮生成已结束，重新加载拿结果
function startPolling() {
  if (pollTimer) return
  polling.value = true
  pollTimer = setInterval(async () => {
    try {
      const res = await getDirectorStatus(novelId, dramaId)
      if (!res.data?.generating) {
        stopPolling()
        notify('漫剧场景已生成完成', 'success')
        await load()
      }
    } catch (e) { /* 忽略轮询错误，下一轮重试 */ }
  }, 5000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  polling.value = false
}

async function generate() {
  if (disabled.value) return
  generating.value = true
  try {
    await generateDirectorScene(novelId, dramaId)
    // 生成即落库：后端已把结果写入数据库，这里直接重载详情拿到已保存 scene，
    // 避免仅前端草稿导致刷新后内容丢失；loaded 后 hasDraft 由 scene 存在与否判断。
    await load()
    notify('导演 Agent 已生成漫剧场景并保存到数据库', 'success')
  } catch (e) {
    notify(e?.message || '生成失败', 'error')
  } finally {
    generating.value = false
  }
}

async function save() {
  if (!scene.value) return
  saving.value = true
  try {
    await saveChapterDramaScene(novelId, dramaId, {
      scene_design: scene.value.scene_design,
      plot_arrangement: scene.value.plot_arrangement,
      camera_movement: scene.value.camera_movement,
      duration_estimate: scene.value.duration_estimate,
      prev_chapter_review: scene.value.prev_chapter_review || '',
      content_raw: scene.value.content_raw || '',
    })
    hasDraft.value = true
    notify('漫剧场景已保存到数据库', 'success')
  } catch (e) {
    notify(e?.message || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}

onMounted(load)
onUnmounted(stopPolling)
</script>

<style scoped>
/* indeterminate 进度条动画：左右往返扫动，表示任务进行中（无确定百分比） */
.indeterminate-bar {
  width: 40%;
  animation: indeterminate 1.2s ease-in-out infinite;
}
@keyframes indeterminate {
  0%   { margin-left: -40%; }
  100% { margin-left: 100%; }
}
</style>
