<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold text-app">MCP 集成</h2>
      <Button @click="openAdd">新增 Server</Button>
    </div>

    <Card>
      <h3 class="font-medium text-app mb-3">已配置 Server</h3>
      <Table :columns="columns" :rows="servers">
        <template #cell-transport="{ value }"><Tag :label="value" /></template>
        <template #cell-status="{ value }">
          <span :style="{ color: value === 'connected' ? 'var(--success)' : 'var(--danger)' }">
            {{ value === 'connected' ? '已连接' : '未连接' }}
          </span>
        </template>
        <template #cell-act="{ row }">
          <div class="flex gap-2">
            <Button variant="ghost" class="!py-1 !px-2" @click="discover(row)">发现工具</Button>
            <Button variant="ghost" class="!py-1 !px-2" @click="health(row)">健康检查</Button>
            <Button variant="ghost" class="!py-1 !px-2 text-danger hover:text-danger" @click="askDelete(row)">删除</Button>
          </div>
        </template>
      </Table>
    </Card>

    <Card>
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-medium text-app">调用日志</h3>
        <Button variant="ghost" @click="loadLogs">刷新</Button>
      </div>
      <Table :columns="logColumns" :rows="logs">
        <template #cell-status="{ value }">
          <span :style="{ color: value === 'success' ? 'var(--success)' : 'var(--danger)' }">{{ value }}</span>
        </template>
      </Table>
    </Card>

    <Modal v-model="showAdd" title="新增 MCP Server">
      <form @submit.prevent="add" class="space-y-4">
        <Input v-model="form.name" label="名称" placeholder="如：本地文件系统" />
        <div>
          <label class="block text-sm text-app mb-1">传输方式</label>
          <select v-model="form.transport" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="stdio">stdio</option>
            <option value="sse">sse</option>
          </select>
        </div>
        <Input v-model="form.url" label="地址 / 命令" placeholder="https://... 或 本地命令" />
        <div>
          <label class="block text-sm text-app mb-1">鉴权方式</label>
          <select v-model="form.auth_type" class="w-full bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent">
            <option value="none">无</option>
            <option value="token">Token</option>
            <option value="basic">Basic</option>
          </select>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" type="button" @click="showAdd = false">取消</Button>
          <Button type="submit" :loading="adding">创建</Button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="delOpen" title="删除 MCP" :message="`确定删除 Server「${pending?.name}」？`" @confirm="doDelete" />
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
import { listMcp, createMcp, discoverMcp, healthMcp, deleteMcp, listMcpLogs } from '@/api/mcp'
import { useToast } from '@/composables/useToast'

const { notify } = useToast()
const servers = ref([])
const logs = ref([])
const showAdd = ref(false)
const adding = ref(false)
const form = ref({ name: '', transport: 'stdio', url: '', auth_type: 'none' })
const columns = [
  { key: 'name', label: '名称' },
  { key: 'transport', label: '传输' },
  { key: 'url', label: '地址' },
  { key: 'status', label: '状态' },
  { key: 'act', label: '操作' }
]
const logColumns = [
  { key: 'server', label: 'Server' },
  { key: 'tool', label: '工具' },
  { key: 'status', label: '状态' },
  { key: 'at', label: '时间' }
]

const delOpen = ref(false)
const pending = ref(null)

onMounted(load)

async function load() {
  const [s, l] = await Promise.all([listMcp(), listMcpLogs()])
  servers.value = s.data || []
  logs.value = l.data || []
}
async function loadLogs() {
  const res = await listMcpLogs()
  logs.value = res.data || []
}
async function discover(row) {
  const res = await discoverMcp(row.id)
  notify(`发现工具：${(res.data?.tools || []).join(', ')}（mock）`, 'success')
}
async function health(row) {
  await healthMcp(row.id)
  notify(`${row.name} 健康检查完成（mock）`, 'success')
}
function openAdd() {
  form.value = { name: '', transport: 'stdio', url: '', auth_type: 'none' }
  showAdd.value = true
}
function askDelete(row) {
  pending.value = row
  delOpen.value = true
}
async function doDelete() {
  if (!pending.value) return
  await deleteMcp(pending.value.id)
  servers.value = servers.value.filter((s) => s.id !== pending.value.id)
  notify(`已删除 Server「${pending.value.name}」`, 'success')
  pending.value = null
}
async function add() {
  adding.value = true
  const res = await createMcp({ ...form.value, status: 'disconnected' })
  if (res.code === 0) {
    servers.value.push(res.data)
    showAdd.value = false
    notify('MCP Server 创建成功', 'success')
  }
  adding.value = false
}
</script>
