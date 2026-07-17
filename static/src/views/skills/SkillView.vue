<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">Skill 管理</h2>
      <div class="flex gap-2">
        <Button variant="ghost" @click="doSeed">补种内置库</Button>
        <Button variant="ghost" @click="doExport">导出</Button>
        <Button variant="ghost" @click="triggerImport">导入</Button>
        <Button @click="openAdd">新建 Skill</Button>
      </div>
    </div>

    <Card>
      <Table :columns="columns" :rows="skills">
        <template #cell-mount_point="{ value }"><Tag :label="mountText(value)" /></template>
        <template #cell-builtin="{ value }">
          <span :style="{ color: value ? 'var(--accent)' : 'var(--text-muted)' }">{{ value ? '内置' : '自定义' }}</span>
        </template>
        <template #cell-enabled="{ row }">
          <label class="switch">
            <input type="checkbox" :checked="row.enabled" @change="toggle(row)" />
            <span>{{ row.enabled ? '启用' : '停用' }}</span>
          </label>
        </template>
        <template #cell-act="{ row }">
          <div class="flex gap-2">
            <Button variant="ghost" class="!py-1 !px-2" @click="openDebug(row)">调试</Button>
            <Button variant="ghost" class="!py-1 !px-2" @click="openEdit(row)">编辑</Button>
            <Button variant="ghost" class="!py-1 !px-2 text-danger hover:text-danger" :disabled="row.builtin" @click="askDelete(row)">删除</Button>
          </div>
        </template>
      </Table>
    </Card>

    <Modal v-model="showForm" :title="editing ? '编辑 Skill' : '新建 Skill'">
      <form @submit.prevent="save" class="space-y-4">
        <Input v-model="form.name" label="名称" placeholder="如：续写风格约束" />
        <div>
          <label class="block text-sm text-app mb-1">挂载点</label>
          <select v-model="form.mount_point" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="global">全局(global)</option>
            <option value="dialogue">对话(dialogue)</option>
            <option value="continue_write">续写(continue_write)</option>
            <option value="extract">抽取(extract)</option>
          </select>
        </div>
        <Input v-model="form.description" label="描述" placeholder="Skill 用途说明" />
        <div>
          <label class="block text-sm text-app mb-1">提示词模板（Jinja2，如 {{ novel_name }}）</label>
          <textarea v-model="form.prompt_template" rows="6"
            class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent font-mono"></textarea>
        </div>
        <Input v-model="form.trigger" label="触发条件" placeholder="如：续写时自动挂载" />
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="showForm = false">取消</Button>
          <Button type="submit" :loading="saving">保存</Button>
        </div>
      </form>
    </Modal>

    <Modal v-model="showDebug" title="调试 Skill">
      <div class="space-y-3">
        <p class="text-sm text-app">可用变量：{{ current.variables?.join('、') || '无' }}</p>
        <div>
          <label class="block text-sm text-app mb-1">变量样例（JSON）</label>
          <textarea v-model="debugContext" rows="4"
            class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent font-mono"></textarea>
        </div>
        <div class="flex gap-2">
          <Button variant="ghost" @click="runDebug(false)">仅渲染预览</Button>
          <Button :loading="debugging" @click="runDebug(true)">实际调用 LLM</Button>
        </div>
        <div v-if="debugResult" class="space-y-2">
          <div>
            <p class="text-xs text-muted mb-1">渲染结果</p>
            <pre class="text-xs bg-base border border-app rounded p-2 whitespace-pre-wrap max-h-40 overflow-auto">{{ debugResult.rendered }}</pre>
          </div>
          <div v-if="debugResult.output">
            <p class="text-xs text-muted mb-1">LLM 输出</p>
            <pre class="text-xs bg-base border border-app rounded p-2 whitespace-pre-wrap max-h-40 overflow-auto">{{ debugResult.output }}</pre>
          </div>
        </div>
      </div>
    </Modal>

    <input ref="fileInput" type="file" accept="application/json" class="hidden" @change="onFile" />
    <ConfirmDialog v-model="delOpen" title="删除 Skill" :message="`确定删除「${pending?.name}」？`" @confirm="doDelete" />
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
import {
  listSkills, createSkill, updateSkill, deleteSkill, toggleSkill,
  debugSkill, seedBuiltin, exportSkills, importSkills,
} from '@/api/skills'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const skills = ref([])
const showForm = ref(false)
const showDebug = ref(false)
const editing = ref(false)
const editingId = ref(null)
const saving = ref(false)
const debugging = ref(false)
const current = ref({})
const debugContext = ref('{}')
const debugResult = ref(null)
const delOpen = ref(false)
const pending = ref(null)
const fileInput = ref(null)

const columns = [
  { key: 'name', label: '名称' },
  { key: 'mount_point', label: '挂载点' },
  { key: 'builtin', label: '类型' },
  { key: 'enabled', label: '状态' },
  { key: 'act', label: '操作' },
]
const mountText = (m) => ({ global: '全局', dialogue: '对话', continue_write: '续写', extract: '抽取' }[m] || m)
const emptyForm = () => ({ name: '', mount_point: 'global', description: '', prompt_template: '', trigger: '' })

onMounted(load)

async function load() {
  const res = await listSkills()
  skills.value = res.data || []
}

function openAdd() {
  editing.value = false
  editingId.value = null
  current.value = emptyForm()
  showForm.value = true
}
function openEdit(row) {
  editing.value = true
  editingId.value = row.id
  current.value = row
  showForm.value = true
}
async function save() {
  saving.value = true
  const payload = { ...current.value }
  const res = editing.value
    ? await updateSkill(editingId.value, payload)
    : await createSkill(payload)
  if (res.code === 0) {
    notify('Skill 已保存', 'success')
    showForm.value = false
    await load()
  }
  saving.value = false
}
async function toggle(row) {
  await toggleSkill(row.id, !row.enabled)
  row.enabled = !row.enabled
}
function askDelete(row) {
  if (row.builtin) return
  pending.value = row
  delOpen.value = true
}
async function doDelete() {
  if (!pending.value) return
  await deleteSkill(pending.value.id)
  notify(`已删除「${pending.value.name}」`, 'success')
  pending.value = null
  await load()
}
async function doSeed() {
  const res = await seedBuiltin()
  notify(`已补种 ${res.data?.count ?? 0} 个内置 Skill`, 'success')
  await load()
}
function openDebug(row) {
  current.value = row
  debugContext.value = '{}'
  debugResult.value = null
  showDebug.value = true
}
async function runDebug(run) {
  debugging.value = true
  try {
    const ctx = JSON.parse(debugContext.value || '{}')
    const res = await debugSkill(current.value.id, ctx, run)
    debugResult.value = res.data
  } catch (e) {
    notify('变量样例 JSON 解析失败', 'error')
  }
  debugging.value = false
}
async function doExport() {
  const res = await exportSkills()
  const blob = new Blob([res.data?.json || '[]'], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = res.data?.filename || 'skills.json'
  a.click()
  notify('已导出 Skill', 'success')
}
function triggerImport() {
  fileInput.value?.click()
}
async function onFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const arr = JSON.parse(text)
    const res = await importSkills(arr)
    notify(`已导入 ${res.data?.count ?? 0} 个 Skill`, 'success')
    await load()
  } catch (err) {
    notify('导入失败：JSON 格式错误', 'error')
  }
  e.target.value = ''
}
</script>
