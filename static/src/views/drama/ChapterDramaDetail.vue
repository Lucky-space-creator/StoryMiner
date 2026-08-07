<template>
  <div class="space-y-5" v-if="drama">
    <!-- 头部：返回 + 标题 + 操作 -->
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push(`/novels/${novelId}/chapter-dramas`)">← 返回章节漫剧列</button>
        <h2 class="text-lg font-semibold text-app">{{ drama.title }}</h2>
        <p class="text-xs text-muted mt-1">
          当前小说：<span class="text-app font-medium">{{ novelName || '加载中…' }}</span>
          · 第 {{ drama.chapter_from }} - {{ drama.chapter_to }} 章
        </p>
      </div>
      <div class="flex gap-2">
        <Button variant="secondary" :loading="generating" @click="generate">生成漫剧场景</Button>
        <Button :disabled="!canSave" :loading="saving" @click="save">保存</Button>
      </div>
    </div>

    <!-- 出场角色 -->
    <div v-if="drama.characters?.length" class="flex flex-wrap gap-2">
      <span v-for="c in drama.characters" :key="c.id"
        class="px-2 py-0.5 rounded-full bg-surface border border-app/20 text-xs text-app">
        {{ c.name }}<span v-if="c.role" class="text-muted"> · {{ c.role }}</span>
      </span>
    </div>
    <p v-if="drama.summary" class="text-sm text-muted leading-relaxed">{{ drama.summary }}</p>

    <!-- 四段式分析：场景设计 / 剧情安排 / 镜头运转 / 预计时长 -->
    <div v-if="scene" class="space-y-4">
      <div class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <h3 class="font-medium text-app mb-3">1. 场景设计</h3>
        <ol v-if="scene.scene_design?.length" class="list-decimal list-inside space-y-2 text-sm text-app leading-relaxed">
          <li v-for="(it, i) in scene.scene_design" :key="i">{{ it }}</li>
        </ol>
        <p v-else class="text-xs text-muted">暂无场景设计</p>
      </div>
      <div class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <h3 class="font-medium text-app mb-3">2. 剧情安排</h3>
        <ol v-if="scene.plot_arrangement?.length" class="list-decimal list-inside space-y-2 text-sm text-app leading-relaxed">
          <li v-for="(it, i) in scene.plot_arrangement" :key="i">{{ it }}</li>
        </ol>
        <p v-else class="text-xs text-muted">暂无剧情安排</p>
      </div>
      <div class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <h3 class="font-medium text-app mb-3">3. 镜头运转</h3>
        <ol v-if="scene.camera_movement?.length" class="list-decimal list-inside space-y-2 text-sm text-app leading-relaxed">
          <li v-for="(it, i) in scene.camera_movement" :key="i">{{ it }}</li>
        </ol>
        <p v-else class="text-xs text-muted">暂无镜头运转</p>
      </div>
      <div class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <h3 class="font-medium text-app mb-2">4. 预计时长</h3>
        <p class="text-sm text-app leading-relaxed">{{ scene.duration_estimate || '（未生成）' }}</p>
      </div>
    </div>
    <div v-else class="bg-surface border border-dashed border-app rounded-[var(--radius-md)] p-10 text-center text-sm text-muted">
      点击右上角「生成漫剧场景」，由导演 Agent 基于章节正文与出场角色生成分镜分析。
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import Button from '@/components/ui/Button.vue'
import { getChapterDrama, generateDirectorScene, saveChapterDramaScene } from '@/api/chapterDramas'
import { getNovel } from '@/api/novels'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const route = useRoute()
const novelId = Number(route.params.id) || null
const dramaId = Number(route.params.dramaId) || null

const drama = ref(null)
const scene = ref(null)
const novelName = ref('')
const generating = ref(false)
const saving = ref(false)

// 草稿（生成但未保存）与已保存共享 scene；canSave 表示当前有可保存的草稿
const hasDraft = ref(false)
const canSave = computed(() => hasDraft.value && !!scene.value)

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
}

async function generate() {
  generating.value = true
  try {
    const res = await generateDirectorScene(novelId, dramaId)
    scene.value = {
      scene_design: res.data.scene_design || [],
      plot_arrangement: res.data.plot_arrangement || [],
      camera_movement: res.data.camera_movement || [],
      duration_estimate: res.data.duration_estimate || '',
      content_raw: res.data.content_raw || '',
    }
    hasDraft.value = true
    notify('导演 Agent 已生成漫剧场景', 'success')
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
</script>
