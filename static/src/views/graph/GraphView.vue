<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push('/novels')">← 返回小说</button>
        <h2 class="text-lg font-semibold text-app">知识图谱</h2>
      </div>
      <div class="flex items-center gap-2 flex-wrap">
        <label class="text-sm text-muted">小说</label>
        <select
          v-model="currentNovel"
          @change="loadGraph"
          class="bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
        >
          <option v-for="n in novels" :key="n.id" :value="n.id">《{{ n.name }}》</option>
        </select>
        <Button @click="openCreateEntity">新增实体</Button>
        <Button @click="openCreateRelation">新增关系</Button>
        <Button @click="handleExtract">抽取实体</Button>
      </div>
    </div>

    <Card>
      <!-- 整体思路：图表容器始终挂载，loading/空数据用绝对定位遮罩覆盖，避免 render 时容器未挂载导致 ECharts 未初始化 -->
      <div class="relative h-[520px]">
        <div ref="graphEl" class="h-full w-full"></div>
        <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-surface/60">
          <Skeleton h="60%" w="80%" />
        </div>
        <div v-else-if="isEmpty" class="absolute inset-0 flex items-center justify-center text-sm text-muted text-center px-4">
          暂未做实体分析，目前没有关系图谱
        </div>
      </div>
    </Card>

    <!-- 实体编辑/新增弹窗 -->
    <EntityModal
      v-model="entityModalOpen"
      :novel-id="currentNovel"
      :entity="editingEntity"
      @saved="onModalSaved"
      @request-delete="onRequestDelete('entity')"
    />
    <!-- 关系编辑/新增弹窗 -->
    <RelationModal
      v-model="relationModalOpen"
      :novel-id="currentNovel"
      :relation="editingRelation"
      :nodes="currentNodes"
      :relation-types="relationTypes"
      @saved="onModalSaved"
      @request-delete="onRequestDelete('relation')"
    />
    <!-- 删除二次确认 -->
    <ConfirmDialog
      v-model="confirmOpen"
      title="确认删除"
      :message="confirmMsg"
      confirm-text="删除"
      @confirm="onConfirmDelete"
    />
    <!-- V13 抽取前确认：提示将删除现有数据 -->
    <ConfirmDialog
      v-model="extractConfirmOpen"
      title="确认重新抽取"
      :message="extractConfirmMsg"
      confirm-text="确认抽取"
      @confirm="onConfirmExtract"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import EntityModal from '@/components/graph/EntityModal.vue'
import RelationModal from '@/components/graph/RelationModal.vue'
import {
  getGraph, extractGraph, getRelationTypes, deleteEntity, deleteRelation, checkGraphExists, getEntityTypes
} from '@/api/graph'
import { listNovels } from '@/api/novels'
import { useToast } from '@/composables/useToast'
import { useTaskProgressStore } from '@/stores/taskProgress'

const { notify } = useToast()
const taskStore = useTaskProgressStore()
const route = useRoute()
const graphEl = ref(null)
const loading = ref(true)
const isEmpty = ref(false)
const novels = ref([])
const currentNovel = ref(Number(route.params.id) || null)
const myTaskId = ref(null)
const currentNodes = ref([])
const relationTypes = ref([])
// 弹窗与删除确认状态
const entityModalOpen = ref(false)
const editingEntity = ref(null)
const relationModalOpen = ref(false)
const editingRelation = ref(null)
const confirmOpen = ref(false)
const confirmMsg = ref('')
let chart = null
let pendingDelete = null  // { kind: 'entity' | 'relation', id }

// V13: 抽取前确认状态
const extractConfirmOpen = ref(false)
const extractConfirmMsg = ref('')

// V13: 7种实体类型颜色映射（与后端 story_entity_type 一致）
const ENTITY_COLORS = {
  character: '#0d9488', place: '#6366f1', org: '#d97706',
  time_period: '#8b5cf6', event: '#dc2626', item: '#16a34a', concept: '#0891b2'
}
// V13: 实体类型 → 节点符号映射
const ENTITY_SYMBOLS = {
  character: 'circle', place: 'rect', org: 'diamond',
  time_period: 'triangle', event: 'roundRect', item: 'pin', concept: 'arrow'
}

onMounted(async () => {
  const res = await listNovels()
  novels.value = res.data?.list || []
  if (!currentNovel.value && novels.value.length) currentNovel.value = novels.value[0].id
  // 关系类型字典供新增/编辑关系时联想
  const rt = await getRelationTypes().catch(() => ({ data: [] }))
  relationTypes.value = rt.data || []
  await loadGraph()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
})

async function loadGraph() {
  // 整体思路：按当前小说拉取知识图谱并渲染。
  // 实现逻辑：置 loading → 调真实接口 → 复位 loading 让容器挂载 → 等 nextTick 确保 DOM 就绪 → 渲染。
  if (!currentNovel.value) return
  loading.value = true
  try {
    const res = await getGraph(currentNovel.value)
    const g = res.data || { nodes: [], links: [], categories: [] }
    currentNodes.value = g.nodes || []
    loading.value = false
    await nextTick()
    render(g)
  } catch (e) {
    loading.value = false
    notify(e?.message || '加载图谱失败', 'error')
  }
}

function render(g) {
  if (!graphEl.value) return
  const hasData = Array.isArray(g.nodes) && g.nodes.length > 0
  isEmpty.value = !hasData
  // 关键点：复用容器前先释放旧实例，避免重复 init 告警
  chart?.dispose()
  chart = echarts.init(graphEl.value)
  // V13: 按实体类型分配颜色与节点符号（7种类型各有专属色和符号）
  const catNames = g.categories?.map(c => c.name) || []
  const catColors = []
  const catSymbols = {}
  for (const c of (g.categories || [])) {
    const code = c.code || ''
    catColors.push(c.color || ENTITY_COLORS[code] || '#888888')
    catSymbols[c.name] = ENTITY_SYMBOLS[code] || 'circle'
  }
  const catIndex = Object.fromEntries(catNames.map((c, i) => [c, i]))
  chart.setOption({
    tooltip: {
      formatter: (p) => p.dataType === 'edge'
        ? `关系：${p.data.relLabel || ''}`
        : `${p.data.name}（${p.data.category || ''}）`
    },
    legend: { data: catNames, bottom: 0, textStyle: { color: '#78716c' } },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        zoom: 1,
        // 关键点：增大斥力与边长，拉开节点间距，避免拥挤
        force: { repulsion: 320, edgeLength: 130, gravity: 0.08, layoutAnimation: true },
        label: { show: true, color: '#1c1917', fontSize: 12 },
        // 关键点：标签防重叠，密集时自动隐藏
        labelLayout: { hideOverlap: true },
        categories: g.categories.map((c) => ({ name: c.name })),
        data: g.nodes.map((n) => ({
          id: n.id,
          name: n.name,
          category: n.category,
          value: n.value,
          symbol: ENTITY_SYMBOLS[n.type] || 'circle',
          // 关键点：节点大小按度数映射，重要角色更突出
          symbolSize: 14 + Math.sqrt(n.value || 0) * 6
        })),
        links: g.links.map((l) => ({
          source: l.source,
          target: l.target,
          id: l.id,
          relLabel: l.label,
          evidence: l.evidence,
          label: { show: true, formatter: l.label || '', color: '#78716c', fontSize: 11 },
          lineStyle: { color: '#cbd5e1', curveness: 0.2, opacity: 0.7, width: 1.5 }
        })),
        lineStyle: { color: '#cbd5e1', curveness: 0.2, opacity: 0.7 },
        itemStyle: { borderColor: '#fff', borderWidth: 1.5, shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.15)' },
        emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
        // V13: 7种实体类型使用专属颜色
        color: catColors.length > 0 ? catColors : ['#0d9488', '#6366f1', '#d97706', '#8b5cf6', '#dc2626', '#16a34a', '#0891b2']
      }
    ]
  }, true)
  // 关键点：每次重建实例后重新绑定点选事件（节点=编辑实体，边=编辑关系）
  chart.off('click')
  chart.on('click', handleChartClick)
}

function handleChartClick(params) {
  if (params.dataType === 'node') {
    editingEntity.value = params.data
    entityModalOpen.value = true
  } else if (params.dataType === 'edge') {
    editingRelation.value = params.data
    relationModalOpen.value = true
  }
}

function openCreateEntity() {
  editingEntity.value = null
  entityModalOpen.value = true
}

function openCreateRelation() {
  editingRelation.value = null
  relationModalOpen.value = true
}

// 弹窗保存成功后重载图谱，保持与数据库同步
function onModalSaved() {
  loadGraph()
}

// 弹窗内「删除」按钮 → 弹出二次确认
function onRequestDelete(kind) {
  pendingDelete = {
    kind,
    id: kind === 'entity' ? editingEntity.value?.id : editingRelation.value?.id
  }
  confirmMsg.value = kind === 'entity'
    ? `确认删除实体「${editingEntity.value?.name}」？其关联的关系也会一并删除。`
    : `确认删除关系「${editingRelation.value?.relLabel || editingRelation.value?.label}」？`
  confirmOpen.value = true
}

async function onConfirmDelete() {
  if (!pendingDelete) return
  try {
    if (pendingDelete.kind === 'entity') {
      await deleteEntity(currentNovel.value, pendingDelete.id)
    } else {
      await deleteRelation(currentNovel.value, pendingDelete.id)
    }
    notify('已删除', 'success')
    entityModalOpen.value = false
    relationModalOpen.value = false
    loadGraph()
  } catch (e) {
    notify(e?.message || '删除失败', 'error')
  } finally {
    pendingDelete = null
  }
}

function resize() {
  chart?.resize()
}

// V13: 抽取前检测已有数据 → 弹确认框 → 确认后提交抽取任务
async function handleExtract() {
  try {
    // 1. 检测是否已有图谱数据
    const statusRes = await checkGraphExists(currentNovel.value)
    const info = statusRes.data || {}
    if (info.has_entities) {
      // 2. 已有数据，弹确认框提示将删除
      extractConfirmMsg.value = `该小说当前已有 ${info.entity_count || 0} 个实体、${info.relation_count || 0} 个关系。重新抽取将删除全部现有图谱数据并重新生成，是否继续？`
      extractConfirmOpen.value = true
    } else {
      // 3. 没有数据，直接抽取
      await doExtract()
    }
  } catch (e) {
    // 检测失败也允许继续抽取
    notify('检测图谱数据失败，仍可继续抽取', 'info')
    await doExtract()
  }
}

// 确认弹窗点击确认后执行
async function onConfirmExtract() {
  extractConfirmOpen.value = false
  await doExtract()
}

// 实际执行抽取（提交后台任务）
async function doExtract() {
  try {
    const res = await extractGraph(currentNovel.value)
    const taskId = res.data?.task_id
    if (!taskId) {
      notify('已触发实体关系抽取，请稍后刷新查看图谱。', 'success')
      return
    }
    myTaskId.value = taskId
    taskStore.upsert({
      id: taskId, type: 'graph',
      name: `小说${novels.value.find(n => n.id === currentNovel.value)?.name || '未知'}-知识图谱抽取`,
      progress: 0, stage: '已提交，正在清空旧数据…', status: 'running'
    })
    taskStore.show()
    notify('已提交，正在后台处理中…', 'info')
  } catch (e) {
    notify(e?.message || '抽取触发失败', 'error')
  }
}

// 监听本页抽取任务完成：由全局轮询更新 store，这里重载图谱
watch(
  () => taskStore.tasks.find((t) => t.id === myTaskId.value)?.status,
  async (s) => {
    if (s === 'success' && myTaskId.value) {
      myTaskId.value = null
      await loadGraph()
    } else if (s === 'failed') {
      myTaskId.value = null
    }
  }
)
</script>
