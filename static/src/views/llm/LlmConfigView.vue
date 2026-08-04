<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">模型管理</h2>
      <div class="flex gap-2">
        <Button variant="ghost" @click="loadUsage">刷新用量</Button>
        <Button @click="openAdd">新增配置</Button>
      </div>
    </div>

    <Card>
      <Table :columns="columns" :rows="configs">
        <template #cell-llm_type="{ value }"><Tag :label="typeText(value)" /></template>
        <template #cell-status="{ value }">
          <span :style="{ color: value === 'active' ? 'var(--success)' : 'var(--danger)' }">
            {{ value === 'active' ? '正常' : '异常' }}
          </span>
        </template>
        <template #cell-is_default="{ value }">
          <Tag v-if="value" label="默认" class="!text-accent" />
          <span v-else class="text-muted">—</span>
        </template>
        <template #cell-act="{ row }">
          <div class="flex gap-2">
            <Button variant="ghost" class="!py-1 !px-2" @click="health(row)">健康检查</Button>
            <Button variant="ghost" class="!py-1 !px-2" @click="openEdit(row)">编辑</Button>
            <Button v-if="!row.is_default" variant="ghost" class="!py-1 !px-2" @click="setDefault(row)">设为默认</Button>
            <Button v-else variant="ghost" class="!py-1 !px-2 text-danger hover:text-danger" @click="cancelDefault(row)">取消默认</Button>
            <Button variant="ghost" class="!py-1 !px-2 text-danger hover:text-danger" @click="askDelete(row)">删除</Button>
          </div>
        </template>
      </Table>
    </Card>

    <Card v-if="usage">
      <h3 class="text-sm font-semibold text-app mb-3">配额与成本（M9.5）</h3>
      <Table :columns="usageColumns" :rows="usage.items">
        <template #cell-cost="{ value }">{{ Number(value).toFixed(4) }}</template>
      </Table>
      <div class="mt-3 flex gap-6 text-sm text-app">
        <span>总调用：{{ usage.total.calls }}</span>
        <span>输入 Token：{{ usage.total.tokens_in }}</span>
        <span>输出 Token：{{ usage.total.tokens_out }}</span>
        <span>总费用：{{ usage.total.cost.toFixed(4) }}</span>
      </div>
    </Card>

    <Modal v-model="showAdd" :title="editing ? '编辑模型配置' : '新增模型配置'">
      <form @submit.prevent="save" class="space-y-4">
        <Input v-model="form.name" label="名称" placeholder="如：GPT-4o" />
        <div>
          <label class="block text-sm text-app mb-1">厂商</label>
          <select v-model="form.provider" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="openai">OpenAI</option>
            <option value="ollama">Ollama（本地）</option>
            <option value="claude">Claude</option>
            <option value="智谱">智谱</option>
            <option value="通义">通义</option>
          </select>
        </div>
        <div>
          <label class="block text-sm text-app mb-1">类型</label>
          <select v-model="form.llm_type" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="chat">对话</option>
            <option value="embed">向量</option>
            <option value="image">图像</option>
          </select>
        </div>
        <Input v-model="form.model" label="模型名" placeholder="如：gpt-4o / qwen2.5:7b" />
        <Input v-model="form.api_key" label="API Key" placeholder="sk-...（Ollama 可留空）" type="password" />
        <Input v-model="form.base_url" label="Base URL" placeholder="https://api.openai.com/v1 或 http://localhost:11434" />
        <div class="grid grid-cols-2 gap-3">
          <Input v-model.number="form.weight" label="降级权重" type="number" />
          <Input v-model.number="form.timeout" label="超时(秒)" type="number" />
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="showAdd = false">取消</Button>
          <Button type="submit" :loading="adding">保存</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除模型" :message="`确定删除模型配置「${pending?.name}」？`" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Table from '@/components/ui/Table.vue'
import Tag from '@/components/ui/Tag.vue'
import Modal from '@/components/ui/Modal.vue'
import Input from '@/components/ui/Input.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { listLlm, createLlm, updateLlm, healthLlm, setDefaultLlm, cancelDefaultLlm, deleteLlm, usageLlm } from '@/api/llmConfigs'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const configs = ref([])
const usage = ref(null)
const showAdd = ref(false)
const editing = ref(false)
const editingId = ref(null)
const adding = ref(false)
const form = ref(emptyForm())
const columns = [
  { key: 'name', label: '名称' },
  { key: 'provider', label: '厂商' },
  { key: 'llm_type', label: '类型' },
  { key: 'model', label: '模型' },
  { key: 'base_url', label: '地址' },
  { key: 'is_default', label: '默认' },
  { key: 'status', label: '状态' },
  { key: 'act', label: '操作' }
]
const usageColumns = [
  { key: 'model', label: '模型' },
  { key: 'task_type', label: '任务类型' },
  { key: 'calls', label: '调用次数' },
  { key: 'tokens_in', label: '输入Token' },
  { key: 'tokens_out', label: '输出Token' },
  { key: 'cost', label: '费用' }
]
const typeText = (t) => ({ chat: '对话', embed: '向量', image: '图像' }[t] || t)

function emptyForm() {
  return { name: '', provider: 'openai', llm_type: 'chat', model: '', api_key: '', base_url: '', weight: 0, timeout: 60 }
}

onMounted(async () => {
  const res = await listLlm()
  configs.value = res.data || []
  await loadUsage()
})

async function loadUsage() {
  try {
    const res = await usageLlm()
    usage.value = res.data || null
  } catch (e) { /* 用量接口失败不影响主列表 */ }
}

function openAdd() {
  editing.value = false
  editingId.value = null
  form.value = emptyForm()
  showAdd.value = true
}

function openEdit(row) {
  editing.value = true
  editingId.value = row.id
  form.value = {
    name: row.name, provider: row.provider, llm_type: row.llm_type,
    model: row.model, api_key: '', base_url: row.base_url || '',
    weight: row.weight ?? 0, timeout: row.timeout ?? 60,
  }
  showAdd.value = true
}

// 健康检查：使用后端真实返回（连通性 + 延迟），并刷新状态列
async function health(row) {
  const res = await healthLlm(row.id)
  const d = res.data || {}
  row.status = d.ok ? 'active' : 'error'
  notify(d.ok ? `${row.name} 健康检查：正常（${d.latency_ms}ms）` : `${row.name} 健康检查：异常（${d.detail}）`, d.ok ? 'success' : 'error')
}

async function setDefault(row) {
  await setDefaultLlm(row.id)
  configs.value.forEach((c) => (c.is_default = c.id === row.id))
  notify(`已将「${row.name}」设为默认`, 'success')
}

async function cancelDefault(row) {
  await cancelDefaultLlm(row.id)
  configs.value.forEach((c) => (c.is_default = false))
  notify(`已取消「${row.name}」的默认设置`, 'success')
}

async function save() {
  adding.value = true
  const payload = { ...form.value }
  if (!payload.api_key) delete payload.api_key  // 编辑时留空则不修改密钥
  const res = editing.value
    ? await updateLlm(editingId.value, payload)
    : await createLlm(payload)
  if (res.code === 0) {
    if (editing.value) {
      const idx = configs.value.findIndex((c) => c.id === editingId.value)
      if (idx >= 0) configs.value[idx] = { ...configs.value[idx], ...res.data }
    } else {
      configs.value.push(res.data)
    }
    showAdd.value = false
    notify('模型配置已保存', 'success')
    await loadUsage()
  }
  adding.value = false
}

const delOpen = ref(false)
const pending = ref(null)
function askDelete(row) {
  pending.value = row
  delOpen.value = true
}
async function doDelete() {
  if (!pending.value) return
  await deleteLlm(pending.value.id)
  configs.value = configs.value.filter((c) => c.id !== pending.value.id)
  notify(`已删除「${pending.value.name}」`, 'success')
  pending.value = null
  await loadUsage()
}
</script>
