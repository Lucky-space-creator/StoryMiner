<template>
  <div class="min-h-[100dvh] bg-app flex">
    <!-- 侧边栏 -->
    <aside class="w-60 shrink-0 border-r border-app bg-surface flex flex-col">
      <div class="h-16 flex items-center gap-2 px-4 border-b border-app">
        <PhBookOpen :size="22" weight="duotone" class="text-accent" />
        <span class="font-semibold text-app truncate">小说解析 RAG</span>
      </div>
      <nav class="flex-1 overflow-y-auto py-3 px-2">
        <template v-for="group in nav" :key="group.title">
          <p class="px-2 pt-4 pb-1 text-[11px] uppercase tracking-wider text-muted">{{ group.title }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            class="flex items-center gap-2 px-2 py-2 rounded-[var(--radius-sm)] text-sm text-app hover:bg-surface2 transition"
            active-class="bg-surface2 text-accent font-medium"
          >
            <component :is="item.icon" :size="18" />
            <span class="truncate">{{ item.label }}</span>
          </RouterLink>
        </template>
      </nav>
      <div class="p-3 border-t border-app">
        <button
          @click="logout"
          class="w-full flex items-center gap-2 px-2 py-2 rounded-[var(--radius-sm)] text-sm text-muted hover:bg-surface2 hover:text-app transition"
        >
          <PhSignOut :size="18" />
          <span>退出登录</span>
        </button>
      </div>
    </aside>

    <!-- 主区 -->
    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-16 border-b border-app bg-surface flex items-center justify-between px-4 shrink-0">
        <div class="text-sm text-muted truncate">{{ route.meta.title || '' }}</div>
        <div class="flex items-center gap-3">
          <button
            @click="theme.toggle()"
            class="p-2 rounded-[var(--radius-sm)] text-muted hover:bg-surface2 hover:text-app transition"
            :title="theme.isDark ? '切换亮色' : '切换暗色'"
          >
            <PhSun v-if="theme.isDark" :size="18" />
            <PhMoon v-else :size="18" />
          </button>
          <div class="text-sm text-app">{{ user.user?.name || '用户' }}</div>
        </div>
      </header>
      <main class="flex-1 overflow-y-auto p-6">
        <div class="max-w-[1400px] mx-auto">
          <slot />
        </div>
      </main>
    </div>

    <!-- M1.11 解析进度悬浮小窗 -->
    <ProgressWindow />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { listNovels } from '@/api/novels'
import { useTaskPoller } from '@/composables/useTaskPoller'
import {
  PhBookOpen, PhSignOut, PhSun, PhMoon,
  PhGauge, PhBooks, PhDatabase, PhParagraph,
  PhGraph, PhUsers, PhPenNib,
  PhCpu, PhPuzzlePiece, PhPlugs, PhFileText, PhCompass,
  PhClock
} from '@phosphor-icons/vue'
import ProgressWindow from '@/components/ui/ProgressWindow.vue'

const route = useRoute()
const router = useRouter()
const user = useUserStore()
const theme = useThemeStore()

// 全局异步任务轮询：每 5s 拉取进行中任务刷新悬浮窗/仪表盘，并在完成时弹窗通知
useTaskPoller(5000)

// 动态取第一个小说，供「知识图谱/人物档案」导航使用，避免写死 id=1 跳到不存在的小说
const novels = ref([])
const firstNovelId = computed(() => novels.value[0]?.id)
onMounted(async () => {
  try {
    const res = await listNovels()
    novels.value = res.data?.list || []
  } catch {}
})

function logout() {
  user.logout()
  router.push('/login')
}

const nav = computed(() => [
  { title: '工作台', items: [
    { to: '/dashboard', label: '仪表盘', icon: PhGauge },
    { to: '/long-tasks', label: '长任务中心', icon: PhClock }
  ] },
  {
    title: '内容',
    items: [
      { to: '/novels', label: '小说', icon: PhBooks },
      { to: '/knowledge-bases', label: '知识库', icon: PhDatabase },
      { to: '/chunks', label: '文档切片', icon: PhParagraph }
    ]
  },
  {
    title: '智能',
    items: [
      { to: firstNovelId.value ? `/novels/${firstNovelId.value}/graph` : '/novels', label: '知识图谱', icon: PhGraph },
      { to: firstNovelId.value ? `/novels/${firstNovelId.value}/characters` : '/novels', label: '人物档案', icon: PhUsers },
      { to: '/writing', label: '续写与概览', icon: PhPenNib }
    ]
  },
  {
    title: '配置',
    items: [
      { to: '/llm-configs', label: '模型管理', icon: PhCpu },
      { to: '/skills', label: 'Skill', icon: PhPuzzlePiece },
      { to: '/mcp', label: 'MCP', icon: PhPlugs },
      { to: '/prompts', label: 'Prompt', icon: PhFileText },
      { to: '/explore', label: '扩展', icon: PhCompass }
    ]
  }
])
</script>
