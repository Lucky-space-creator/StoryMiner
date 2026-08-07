import { createRouter, createWebHistory } from 'vue-router'
import Login from '@/views/auth/Login.vue'
import Dashboard from '@/views/Dashboard.vue'
import LongTaskCenter from '@/views/LongTaskCenter.vue'
import NovelList from '@/views/novels/NovelList.vue'
import NovelDetail from '@/views/novels/NovelDetail.vue'
import NovelReader from '@/views/novels/NovelReader.vue'
import KBList from '@/views/kb/KBList.vue'
import KBDetail from '@/views/kb/KBDetail.vue'
import ChunkBrowser from '@/views/chunks/ChunkBrowser.vue'
import GraphView from '@/views/graph/GraphView.vue'
import CharacterView from '@/views/characters/CharacterView.vue'
import ChapterDramaView from '@/views/drama/ChapterDramaView.vue'
import ChapterDramaDetail from '@/views/drama/ChapterDramaDetail.vue'
import WritingView from '@/views/writing/WritingView.vue'
import LlmConfigView from '@/views/llm/LlmConfigView.vue'
import SkillView from '@/views/skills/SkillView.vue'
import McpView from '@/views/mcp/McpView.vue'
import ExploreView from '@/views/explore/ExploreView.vue'

// 路由全定义；未实现的模块暂用 Placeholder，后续逐批替换
const routes = [
  { path: '/login', name: 'login', component: Login, meta: { title: '登录', requiresAuth: false } },
  { path: '/register', name: 'register', component: Login, meta: { title: '注册', requiresAuth: false } },
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'dashboard', component: Dashboard, meta: { title: '仪表盘', requiresAuth: true } },
  { path: '/long-tasks', name: 'long-tasks', component: LongTaskCenter, meta: { title: '长任务中心', requiresAuth: true } },
  { path: '/novels', name: 'novels', component: NovelList, meta: { title: '小说', requiresAuth: true } },
  { path: '/novels/:id', name: 'novel-detail', component: NovelDetail, meta: { title: '小说详情', requiresAuth: true } },
  { path: '/novels/:id/read', name: 'novel-reader', component: NovelReader, meta: { title: '阅读小说', requiresAuth: true } },
  { path: '/knowledge-bases', name: 'kbs', component: KBList, meta: { title: '知识库', requiresAuth: true } },
  { path: '/knowledge-bases/:id', name: 'kb-detail', component: KBDetail, meta: { title: '知识库详情', requiresAuth: true } },
  { path: '/chunks', name: 'chunks', component: ChunkBrowser, meta: { title: '文档切片', requiresAuth: true } },
  { path: '/novels/:id/graph', name: 'graph', component: GraphView, meta: { title: '知识图谱', requiresAuth: true } },
  { path: '/novels/:id/characters', name: 'characters', component: CharacterView, meta: { title: '人物档案', requiresAuth: true } },
  { path: '/novels/:id/chapter-dramas', name: 'chapter-dramas', component: ChapterDramaView, meta: { title: '章节漫剧', requiresAuth: true } },
  { path: '/novels/:id/chapter-dramas/:dramaId', name: 'chapter-drama-detail', component: ChapterDramaDetail, meta: { title: '漫剧场景', requiresAuth: true } },
  { path: '/writing', name: 'writing', component: WritingView, meta: { title: '续写与概览', requiresAuth: true } },
  { path: '/llm-configs', name: 'llm', component: LlmConfigView, meta: { title: '模型管理', requiresAuth: true } },
  { path: '/skills', name: 'skills', component: SkillView, meta: { title: 'Skill 管理', requiresAuth: true } },
  { path: '/mcp', name: 'mcp', component: McpView, meta: { title: 'MCP 集成', requiresAuth: true } },
  { path: '/explore', name: 'explore', component: ExploreView, meta: { title: '扩展功能', requiresAuth: true } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) return '/login'
  if ((to.name === 'login' || to.name === 'register') && token) return '/dashboard'
})

export default router
