<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <div>
        <button class="text-sm text-muted hover:text-app mb-1" @click="$router.push('/novels')">← 返回小说</button>
        <h2 class="text-lg font-semibold text-app">知识图谱</h2>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-sm text-muted">小说</label>
        <select
          v-model="currentNovel"
          @change="loadGraph"
          class="bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
        >
          <option v-for="n in novels" :key="n.id" :value="n.id">《{{ n.name }}》</option>
        </select>
        <Button @click="extract">抽取实体</Button>
      </div>
    </div>

    <Card>
      <div v-if="loading" class="h-[520px] flex items-center justify-center">
        <Skeleton h="60%" w="80%" />
      </div>
      <div v-else ref="graphEl" class="h-[520px]"></div>
    </Card>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { getGraph, extractGraph } from '@/api/graph'
import { listNovels } from '@/api/novels'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const route = useRoute()
const graphEl = ref(null)
const loading = ref(true)
const novels = ref([])
const currentNovel = ref(Number(route.params.id) || null)
let chart = null

onMounted(async () => {
  const res = await listNovels()
  novels.value = res.data?.list || []
  if (!currentNovel.value && novels.value.length) currentNovel.value = novels.value[0].id
  await loadGraph()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
})

async function loadGraph() {
  // 整体思路：按当前小说拉取知识图谱并渲染。
  // 实现逻辑：置 loading → 调真实接口 → 渲染或捕获异常弹窗 → 复位 loading。
  if (!currentNovel.value) return
  loading.value = true
  try {
    const res = await getGraph(currentNovel.value)
    const g = res.data || { nodes: [], links: [], categories: [] }
    render(g)
    window.addEventListener('resize', resize)
  } catch (e) {
    notify(e?.message || '加载图谱失败', 'error')
  } finally {
    loading.value = false
  }
}

function render(g) {
  if (!graphEl.value) return
  const nameById = Object.fromEntries(g.nodes.map((n) => [n.id, n.name]))
  chart = echarts.init(graphEl.value)
  chart.setOption({
    tooltip: {},
    legend: { data: g.categories, bottom: 0, textStyle: { color: '#78716c' } },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        label: { show: true, color: '#1c1917' },
        categories: g.categories.map((c) => ({ name: c })),
        data: g.nodes.map((n) => ({ name: n.name, category: n.category, value: n.value })),
        links: g.links.map((l) => ({
          source: nameById[l.source],
          target: nameById[l.target],
          label: { show: true, formatter: l.label, color: '#78716c', fontSize: 11 }
        })),
        lineStyle: { color: '#d6d3d1', curveness: 0.1 },
        emphasis: { focus: 'adjacency' },
        color: ['#0d9488', '#2dd4bf', '#d97706', '#dc2626', '#6366f1']
      }
    ]
  })
}

function resize() {
  chart?.resize()
}

async function extract() {
  // 实现逻辑：触发后台抽取，成功/失败均弹窗提示；图谱需刷新后查看。
  try {
    await extractGraph(currentNovel.value)
    notify('已触发实体关系抽取，请稍后刷新查看图谱。', 'success')
  } catch (e) {
    notify(e?.message || '抽取触发失败', 'error')
  }
}
</script>
