<template>
  <!--
    阅读辅助对话侧边栏（M3）
    整体思路：以右侧滑出面板呈现「阅读助手」，复用当前小说的对话历史，支持模型切换、
    上下文窗口设置、附件上传与流式回答。
    关键点：消息按角色分左右气泡；流式 delta 实时追加到最后一条助手消息；
    上下文压缩对前端透明（后端基于 context_window 自动摘要）。
    实现逻辑：打开即拉取历史与模型列表 -> 输入经 WebSocket 流式生成 -> done 后回拉历史校准。
  -->
  <transition name="slide">
    <div
      v-if="visible"
      class="fixed top-0 right-0 h-full w-[380px] max-w-[90vw] bg-surface border-l border-stone-200 shadow-xl flex flex-col z-40"
    >
      <!-- 头部 -->
      <div class="flex items-center justify-between px-4 py-3 border-b border-stone-200 shrink-0">
        <div class="flex items-center gap-2">
          <span class="text-base">📖</span>
          <span class="font-medium text-app text-sm">阅读助手</span>
        </div>
        <div class="flex items-center gap-1">
          <button class="p-1.5 rounded hover:bg-surface2 text-muted" title="设置" @click="showSettings = !showSettings">⚙</button>
          <button class="p-1.5 rounded hover:bg-surface2 text-muted" title="清空对话" @click="onClear">🗑</button>
          <button class="p-1.5 rounded hover:bg-surface2 text-muted" title="关闭" @click="$emit('close')">✕</button>
        </div>
      </div>

      <!-- 设置区 -->
      <div v-if="showSettings" class="px-4 py-3 border-b border-stone-200 space-y-2 text-xs shrink-0 bg-surface2/40">
        <div class="flex items-center gap-2">
          <label class="w-16 text-muted shrink-0">模型</label>
          <select v-model="settings.llm_config_id" class="flex-1 border border-stone-200 rounded px-2 py-1 bg-surface text-app">
            <option :value="null">默认（用户默认模型）</option>
            <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}（{{ m.model }}）</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <label class="w-16 text-muted shrink-0">保留条数</label>
          <input v-model.number="settings.keep_recent" type="number" min="1" class="flex-1 border border-stone-200 rounded px-2 py-1 bg-surface text-app" />
        </div>
        <div class="flex items-center gap-2">
          <label class="w-16 text-muted shrink-0">上下文窗</label>
          <input v-model.number="settings.context_window" type="number" min="500" step="500" class="flex-1 border border-stone-200 rounded px-2 py-1 bg-surface text-app" />
        </div>
        <button class="w-full text-accent hover:text-app py-1" @click="saveSettings">保存设置</button>
      </div>

      <!-- 消息列表 -->
      <div ref="listEl" class="flex-1 overflow-y-auto px-3 py-4 space-y-3">
        <p v-if="!messages.length" class="text-xs text-muted text-center py-8">
          向阅读助手提问吧，例如「这一章主角为什么离开？」
        </p>
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="flex"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <div
            class="max-w-[85%] rounded-lg px-3 py-2 text-sm leading-6 whitespace-pre-wrap break-words"
            :class="msg.role === 'user' ? 'bg-teal-600 text-white' : 'bg-surface2 text-app'"
          >
            <div>{{ msg.content }}</div>
            <div v-if="msg.attachments && msg.attachments.length" class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="(a, i) in msg.attachments"
                :key="i"
                class="inline-block text-[11px] px-1.5 py-0.5 rounded bg-black/10"
              >📎 {{ a.name || a.url }}</span>
            </div>
          </div>
        </div>
        <div v-if="sending" class="flex justify-start">
          <div class="max-w-[85%] rounded-lg px-3 py-2 text-sm bg-surface2 text-app">
            <span class="inline-block w-2 h-2 bg-muted rounded-full animate-pulse"></span>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="border-t border-stone-200 p-3 shrink-0">
        <div v-if="pendingAttach.length" class="flex flex-wrap gap-1 mb-2">
          <span
            v-for="(a, i) in pendingAttach"
            :key="i"
            class="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-surface2"
          >
            📎 {{ a.name }}
            <button class="text-muted hover:text-app" @click="pendingAttach.splice(i, 1)">✕</button>
          </span>
        </div>
        <div class="flex items-end gap-2">
          <button class="p-2 rounded hover:bg-surface2 text-muted shrink-0" title="上传附件" @click="$refs.fileInput.click()">📎</button>
          <input ref="fileInput" type="file" class="hidden" @change="onFile" />
          <textarea
            v-model="input"
            rows="2"
            placeholder="向阅读助手提问…"
            class="flex-1 resize-none border border-stone-200 rounded px-2 py-1.5 text-sm text-app bg-surface focus:outline-none focus:ring-1 focus:ring-teal-400"
            @keydown.enter.exact.prevent="onSend"
          ></textarea>
          <button
            class="px-3 py-2 rounded bg-teal-600 text-white text-sm hover:bg-teal-700 disabled:opacity-50 shrink-0"
            :disabled="sending || !input.trim()"
            @click="onSend"
          >发送</button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
// 整体思路：面板打开时拉取历史与模型；发送经 WebSocket 流式生成，done 后回拉历史校准顺序与 id。
// 关键点：流式过程维护本地临时助手消息用于实时渲染；出错则提示并丢弃空助手消息。
// 实现逻辑：open() 拉数据 -> onSend() 建 socket 发消息 -> handleFrame() 处理 delta/done/error。
import { ref, watch, nextTick } from 'vue'
import { listLlm } from '@/api/llmConfigs'
import {
  getReadingChat,
  updateReadingChat,
  clearReadingChat,
  uploadReadingAttachment,
  openReadingChatSocket,
} from '@/api/readingChat'
import { useToast } from '@/composables/useToast'

const props = defineProps({
  novelId: { type: [Number, String], required: true },
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])
const { notify } = useToast()

const messages = ref([])
const models = ref([])
const settings = ref({ llm_config_id: null, keep_recent: 10, context_window: 4000, system_prompt: '' })
const input = ref('')
const pendingAttach = ref([])
const sending = ref(false)
const showSettings = ref(false)
const listEl = ref(null)

let socket = null

watch(() => props.visible, (v) => {
  if (v) open()
  else closeSocket()
})

async function open() {
  await loadModels()
  await loadChat()
}

async function loadModels() {
  try {
    const res = await listLlm({ llm_type: 'chat' })
    models.value = res.data || []
  } catch (e) {
    models.value = []
  }
}

async function loadChat() {
  try {
    const res = await getReadingChat(props.novelId)
    const d = res.data || {}
    messages.value = d.messages || []
    if (d.session) {
      settings.value = {
        llm_config_id: d.session.llm_config_id ?? null,
        keep_recent: d.session.keep_recent ?? 10,
        context_window: d.session.context_window ?? 4000,
        system_prompt: d.session.system_prompt || '',
      }
    }
    scrollToBottom()
  } catch (e) {
    notify(e.message || '加载对话失败', 'error')
  }
}

async function saveSettings() {
  try {
    await updateReadingChat(props.novelId, settings.value)
    notify('设置已保存', 'success')
    showSettings.value = false
  } catch (e) {
    notify(e.message || '保存失败', 'error')
  }
}

async function onClear() {
  if (!confirm('确定清空当前小说的阅读对话历史？')) return
  try {
    await clearReadingChat(props.novelId)
    messages.value = []
  } catch (e) {
    notify(e.message || '清空失败', 'error')
  }
}

function ensureSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) return socket
  const token = localStorage.getItem('token')
  socket = openReadingChatSocket(props.novelId, token)
  socket.onmessage = (ev) => {
    let frame
    try { frame = JSON.parse(ev.data) } catch { return }
    handleFrame(frame)
  }
  socket.onclose = () => { socket = null }
  socket.onerror = () => { socket = null }
  return socket
}

function handleFrame(frame) {
  if (frame.type === 'delta') {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last._streaming) {
      last.content += frame.text
    } else {
      messages.value.push({ id: `tmp-${Date.now()}`, role: 'assistant', content: frame.text, _streaming: true })
    }
    scrollToBottom()
  } else if (frame.type === 'done') {
    sending.value = false
    // 回拉历史以校准 id 与顺序
    loadChat()
  } else if (frame.type === 'error') {
    sending.value = false
    const last = messages.value[messages.value.length - 1]
    if (last && last._streaming && !last.content) messages.value.pop()
    notify(frame.message || '生成失败', 'error')
  }
}

async function onSend() {
  const text = input.value.trim()
  if (!text || sending.value) return
  // 本地先展示用户消息
  const attachments = pendingAttach.value.slice()
  messages.value.push({
    id: `u-${Date.now()}`,
    role: 'user',
    content: text,
    attachments,
  })
  input.value = ''
  pendingAttach.value = []
  sending.value = true
  scrollToBottom()

  const sock = ensureSocket()
  const send = () => {
    sock.send(JSON.stringify({
      content: text,
      attachments,
      llm_config_id: settings.value.llm_config_id ?? null,
    }))
  }
  if (sock.readyState === WebSocket.OPEN) {
    send()
  } else {
    sock.onopen = send
  }
}

async function onFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    const res = await uploadReadingAttachment(props.novelId, file)
    const d = res.data || {}
    pendingAttach.value.push({ name: d.name || file.name, url: d.key, size: d.size })
  } catch (err) {
    notify(err.message || '附件上传失败', 'error')
  } finally {
    e.target.value = ''
  }
}

function closeSocket() {
  if (socket) {
    try { socket.close() } catch {}
    socket = null
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  })
}
</script>

<style scoped>
.slide-enter-active, .slide-leave-active { transition: transform .25s ease; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
</style>
