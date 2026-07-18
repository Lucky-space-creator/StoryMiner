<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">知识库</h2>
      <Button @click="showCreate = true">新建知识库</Button>
    </div>

    <div v-if="loading" class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div v-for="i in 4" :key="i" class="bg-surface border border-app rounded-[var(--radius-md)] p-5">
        <Skeleton h="1.25rem" w="60%" />
        <Skeleton h="0.875rem" w="40%" class="mt-3" />
        <Skeleton h="0.75rem" w="50%" class="mt-4" />
      </div>
    </div>

    <EmptyState
      v-else-if="kbs.length === 0"
      title="还没有知识库"
      desc="从小说创建知识库，开始切片与向量化"
      :icon="PhDatabase"
    >
      <Button @click="showCreate = true">新建知识库</Button>
    </EmptyState>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div
        v-for="kb in kbs"
        :key="kb.id"
        @click="$router.push(`/knowledge-bases/${kb.id}`)"
        class="bg-surface border border-app rounded-[var(--radius-md)] p-5 hover:border-accent transition cursor-pointer"
      >
        <div class="flex items-center justify-between gap-2">
          <h3 class="font-medium text-app truncate">{{ kb.name }}</h3>
          <Tag :label="scopeText(kb.scope)" />
        </div>
        <p class="text-sm text-muted mt-1">小说：{{ kb.novel_name }}</p>
        <div class="flex gap-4 mt-3 text-sm text-muted">
          <span>{{ kb.doc_count }} 文档</span>
          <span>{{ kb.chunk_count }} 切片</span>
          <span>{{ formatChars(kb.chars) }}</span>
        </div>
        <p class="text-xs text-muted mt-3">更新于 {{ formatDateTime(kb.updated_at) }}</p>
      </div>
    </div>

    <Modal v-model="showCreate" title="新建知识库">
      <form @submit.prevent="submit" class="space-y-4">
        <div>
          <label class="block text-sm text-app mb-1">关联小说</label>
          <select
            v-model="form.novel_id"
            class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
          >
            <option v-for="n in novels" :key="n.id" :value="n.id">{{ n.name }}</option>
          </select>
        </div>
        <Input v-model="form.name" label="知识库名称" placeholder="如：剑来-全本知识库" />
        <div>
          <label class="block text-sm text-app mb-1">范围</label>
          <select
            v-model="form.scope"
            class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
          >
            <option value="full">全本</option>
            <option value="custom">自定义</option>
          </select>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="showCreate = false">取消</Button>
          <Button type="submit" :loading="creating">创建</Button>
        </div>
      </form>
    </Modal>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { PhDatabase } from '@phosphor-icons/vue'
import { useToast } from '@/composables/useToast'
import Button from '@/components/ui/Button.vue'
import Tag from '@/components/ui/Tag.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import Modal from '@/components/ui/Modal.vue'
import Input from '@/components/ui/Input.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import { listKBs, createKB } from '@/api/knowledgeBases'
import { listNovels } from '@/api/novels'
import { formatDateTime } from '@/utils/datetime'

const route = useRoute()
const { notify } = useToast()
const kbs = ref([])
const novels = ref([])
const loading = ref(true)
const showCreate = ref(false)
const creating = ref(false)
const form = ref({ novel_id: null, name: '', scope: 'full' })

const scopeMap = { full: '全本', custom: '自定义' }
const scopeText = (s) => scopeMap[s] || s
function formatChars(n) {
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万字` : `${n}字`
}

onMounted(async () => {
  const [kbRes, novelRes] = await Promise.all([listKBs(), listNovels()])
  kbs.value = kbRes.data?.list || []
  novels.value = novelRes.data?.list || []
  // 优先用入口携带的小说 id（如从小说详情「建知识库」带参进入），否则默认第一个
  const qid = Number(route.query.novel_id)
  form.value.novel_id = (novels.value.find((n) => n.id === qid) ? qid : null) || novels.value[0]?.id || null
  loading.value = false
})

async function submit() {
  if (!form.value.novel_id) {
    notify('请先选择关联小说', 'error')
    return
  }
  creating.value = true
  const res = await createKB({ ...form.value })
  if (res.code === 0) {
    const novel = novels.value.find((n) => n.id === Number(form.value.novel_id))
    kbs.value.push({
      id: res.data.id,
      novel_name: novel?.name || '未知',
      name: form.value.name,
      scope: form.value.scope,
      doc_count: 0,
      chunk_count: 0,
      chars: 0,
      updated_at: res.data?.updated_at || new Date().toISOString()
    })
    showCreate.value = false
    form.value = { novel_id: novels.value[0]?.id || null, name: '', scope: 'full' }
  }
  creating.value = false
}
</script>
