<template>
  <!--
  小说阅读器：章节导航 + 正文展示。
  整体思路：左侧章节列表、右侧正文区域，支持上一章/下一章快速切换。
  关键点：通过路由 chapter_id 定位当前章节；列表按 chapter_no 排序。
  -->
  <div class="flex h-[calc(100vh-8rem)] gap-4" v-if="novel">
    <!-- 左侧章节列表 -->
    <aside class="w-56 shrink-0 overflow-y-auto border border-stone-200 rounded-[var(--radius-sm)] bg-surface">
      <div class="p-3 border-b border-stone-200">
        <button class="text-sm text-muted hover:text-app" @click="$router.push(`/novels/${novel.id}`)">
          ← 返回详情
        </button>
        <h3 class="font-medium text-app text-sm mt-1 truncate">{{ novel.name }}</h3>
      </div>
      <div class="p-1">
        <div
          v-for="ch in chapters"
          :key="ch.id"
          @click="selectChapter(ch)"
          class="px-3 py-2 text-sm cursor-pointer rounded hover:bg-surface2 transition"
          :class="currentChapter?.id === ch.id ? 'bg-teal-50 text-teal-700 font-medium' : 'text-app'"
        >
          {{ ch.title || ('第' + ch.chapter_no + '章') }}
          <span class="text-xs text-muted ml-1">{{ ch.word_count }}字</span>
        </div>
        <p v-if="!chapters.length" class="text-xs text-muted text-center py-4">暂无章节</p>
      </div>
      <!-- 分页 -->
      <div v-if="total > pageSize" class="flex justify-between px-3 py-2 border-t border-stone-200">
        <button
          :disabled="page <= 1"
          @click="loadChapters(page - 1)"
          class="text-xs text-accent hover:text-app disabled:text-muted"
        >上一页</button>
        <span class="text-xs text-muted">{{ page }}/{{ totalPages }}</span>
        <button
          :disabled="page >= totalPages"
          @click="loadChapters(page + 1)"
          class="text-xs text-accent hover:text-app disabled:text-muted"
        >下一页</button>
      </div>
    </aside>

    <!-- 右侧正文区域 -->
    <main class="flex-1 flex flex-col overflow-hidden">
      <!-- 章节标题栏 + 导航 -->
      <div class="flex items-center justify-between mb-3 shrink-0">
        <div>
          <h2 class="text-lg font-semibold text-app">
            {{ currentChapter?.title || ('第' + (currentChapter?.chapter_no || 0) + '章') }}
          </h2>
          <p class="text-xs text-muted">字数：{{ currentChapter?.word_count || 0 }}</p>
        </div>
        <div class="flex gap-2">
          <Button size="sm" variant="ghost" :disabled="!prevChapter" @click="prevChapter && selectChapter(prevChapter)">
            ← 上一章
          </Button>
          <Button size="sm" variant="ghost" :disabled="!nextChapter" @click="nextChapter && selectChapter(nextChapter)">
            下一章 →
          </Button>
        </div>
      </div>

      <!-- 章节正文（可滚动） -->
      <div v-if="chapterContent !== null" class="flex-1 overflow-y-auto border border-stone-200 rounded-[var(--radius-sm)] bg-surface p-6">
        <div class="max-w-3xl mx-auto">
          <p v-if="loadingContent" class="text-sm text-muted text-center py-8">加载中...</p>
          <div v-else class="text-sm text-app leading-8 whitespace-pre-wrap">
            {{ chapterContent }}
          </div>
        </div>
      </div>
      <div v-else class="flex-1 flex items-center justify-center border border-stone-200 rounded-[var(--radius-sm)] bg-surface">
        <p class="text-sm text-muted">请从左侧选择章节开始阅读</p>
      </div>

      <!-- 底部导航 -->
      <div class="flex justify-between mt-3 shrink-0">
        <Button size="sm" variant="ghost" :disabled="!prevChapter" @click="prevChapter && selectChapter(prevChapter)">
          ← 上一章
        </Button>
        <span class="text-xs text-muted">{{ chapterIndex + 1 }} / {{ chapters.length }}</span>
        <Button size="sm" variant="ghost" :disabled="!nextChapter" @click="nextChapter && selectChapter(nextChapter)">
          下一章 →
        </Button>
      </div>
    </main>
  </div>
</template>

<script setup>
// 整体思路：进入页面加载小说信息与章节列表，默认选中第一章或 URL 中指定的章节。
// 关键点：章节正文按需加载（选中章节后才请求详情），减少初始数据传输量。
// 实现逻辑：fetch 小说 → fetch 分页章节列表 → 自动选中第一个或指定章节 → 加载正文。
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from '@/components/ui/Button.vue'
import { getNovel, listChapters, getChapter } from '@/api/novels'
import { useToast } from '@/composables/useToast'

const route = useRoute()
const router = useRouter()
const { notify } = useToast()

const novel = ref(null)
const chapters = ref([])
const currentChapter = ref(null)
const chapterContent = ref(null)
const loadingContent = ref(false)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

const chapterIndex = computed(() => {
  if (!currentChapter.value) return -1
  return chapters.value.findIndex(c => c.id === currentChapter.value.id)
})

const prevChapter = computed(() => {
  const idx = chapterIndex.value
  if (idx <= 0) return null
  return chapters.value[idx - 1]
})

const nextChapter = computed(() => {
  const idx = chapterIndex.value
  if (idx < 0 || idx >= chapters.value.length - 1) return null
  return chapters.value[idx + 1]
})

async function loadChapters(p = 1) {
  try {
    const res = await listChapters(route.params.id, { page: p, size: pageSize.value })
    const d = res.data || {}
    chapters.value = d.list || []
    total.value = d.total || 0
    page.value = d.page || p
    pageSize.value = d.size || pageSize.value
  } catch (e) {
    notify(e.message || '加载章节列表失败', 'error')
  }
}

async function selectChapter(ch) {
  currentChapter.value = ch
  chapterContent.value = null
  loadingContent.value = true
  try {
    const res = await getChapter(ch.id)
    chapterContent.value = res.data?.content || ''
  } catch (e) {
    notify(e.message || '加载章节内容失败', 'error')
    chapterContent.value = ''
  } finally {
    loadingContent.value = false
  }
}

onMounted(async () => {
  try {
    const nRes = await getNovel(route.params.id)
    novel.value = nRes.data || null
    await loadChapters(1)
    // 自动选中第一章
    if (chapters.value.length > 0) {
      await selectChapter(chapters.value[0])
    }
  } catch (e) {
    notify(e.message || '加载小说失败', 'error')
  }
})
</script>
