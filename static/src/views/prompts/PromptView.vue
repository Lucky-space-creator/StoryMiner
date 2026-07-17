<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">Prompt 模板</h2>
      <Button @click="openAdd">新建模板</Button>
    </div>

    <Card>
      <Table :columns="columns" :rows="templates">
        <template #cell-type="{ value }"><Tag :label="typeText(value)" /></template>
        <template #cell-is_default="{ value }">
          <span :style="{ color: value ? 'var(--success)' : 'var(--text-muted)' }">{{ value ? '默认' : '—' }}</span>
        </template>
        <template #cell-act="{ row }">
          <div class="flex gap-2">
            <Button variant="ghost" class="!py-1 !px-2" @click="setDefault(row)" v-if="!row.is_default">设为默认</Button>
            <Button variant="ghost" class="!py-1 !px-2 text-danger hover:text-danger" @click="askDelete(row)">删除</Button>
          </div>
        </template>
      </Table>
    </Card>

    <Modal v-model="showAdd" title="新建 Prompt 模板">
      <form @submit.prevent="add" class="space-y-4">
        <Input v-model="form.name" label="名称" placeholder="如：续写提示词" />
        <div>
          <label class="block text-sm text-app mb-1">类型</label>
          <select v-model="form.type" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="continue">续写</option>
            <option value="chat">对话</option>
            <option value="summary">摘要</option>
          </select>
        </div>
        <div>
          <label class="block text-sm text-app mb-1">模板内容</label>
          <textarea
            v-model="form.content"
            rows="5"
            placeholder="支持 {{character}}、{{context}} 等 Jinja2 变量"
            class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent resize-y text-sm"
          ></textarea>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="showAdd = false">取消</Button>
          <Button type="submit" :loading="adding">创建</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除模板" :message="`确定删除 Prompt 模板「${pending?.name}」？`" @confirm="doDelete" />
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
import { listPrompts, createPrompt, deletePrompt, setDefaultPrompt } from '@/api/prompts'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const templates = ref([])
const showAdd = ref(false)
const adding = ref(false)
const form = ref({ name: '', type: 'continue', content: '' })
const columns = [
  { key: 'name', label: '名称' },
  { key: 'type', label: '类型' },
  { key: 'version', label: '版本' },
  { key: 'is_default', label: '默认' },
  { key: 'act', label: '操作' }
]
const typeText = (t) => ({ continue: '续写', chat: '对话', summary: '摘要' }[t] || t)

const delOpen = ref(false)
const pending = ref(null)

onMounted(async () => {
  const res = await listPrompts()
  templates.value = res.data || []
})

function openAdd() {
  form.value = { name: '', type: 'continue', content: '' }
  showAdd.value = true
}
async function setDefault(row) {
  await setDefaultPrompt(row.id)
  templates.value.forEach((t) => (t.is_default = t.id === row.id))
  notify(`已将「${row.name}」设为默认`, 'success')
}
function askDelete(row) {
  pending.value = row
  delOpen.value = true
}
async function doDelete() {
  if (!pending.value) return
  await deletePrompt(pending.value.id)
  templates.value = templates.value.filter((t) => t.id !== pending.value.id)
  notify(`已删除「${pending.value.name}」`, 'success')
  pending.value = null
}
async function add() {
  adding.value = true
  const res = await createPrompt({ ...form.value, version: 1, is_default: false })
  if (res.code === 0) {
    templates.value.push(res.data)
    showAdd.value = false
    notify('Prompt 模板创建成功', 'success')
  }
  adding.value = false
}
</script>
