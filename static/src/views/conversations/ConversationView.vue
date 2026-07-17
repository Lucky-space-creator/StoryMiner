<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-lg font-semibold text-app">角色对话</h2>
        <p class="text-sm text-muted">与小说人物沉浸式对话，可新建会话并自由选角（支持群聊）</p>
      </div>
      <Button @click="openCreate"><PhPlus :size="16" class="mr-1" />新建对话</Button>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-4">
      <!-- 会话列表 -->
      <div class="bg-surface border border-app rounded-[var(--radius-md)] p-2 self-start">
        <div v-if="loading" class="space-y-2 p-2">
          <div v-for="i in 3" :key="i" class="h-14 bg-surface2 rounded-[var(--radius-sm)] animate-pulse"></div>
        </div>
        <div v-else-if="conversations.length === 0" class="p-6 text-center text-sm text-muted">
          还没有会话，点击右上角「新建对话」开始
        </div>
        <div v-else class="space-y-1 max-h-[620px] overflow-y-auto">
          <div
            v-for="c in conversations"
            :key="c.id"
            @click="select(c)"
            class="group flex items-center gap-3 px-3 py-2 rounded-[var(--radius-sm)] cursor-pointer transition"
            :class="active?.id === c.id ? 'bg-surface2' : 'hover:bg-surface2'"
          >
            <Avatar :name="(c.characters?.[0]?.name) || '群'" :count="c.characters?.length || 1" />
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium text-app truncate">{{ c.title }}</div>
              <div class="text-xs text-muted truncate">
                {{ (c.characters || []).map((ch) => ch.name).join('、') }} · 《{{ c.novel_name }}》
              </div>
            </div>
            <button
              class="opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition"
              @click.stop="remove(c)"
              title="删除会话"
            ><PhTrash :size="15" /></button>
          </div>
        </div>
      </div>

      <!-- 聊天区 -->
      <div class="bg-surface border border-app rounded-[var(--radius-md)] flex flex-col h-[640px] overflow-hidden">
        <template v-if="!active">
          <div class="flex-1 flex flex-col items-center justify-center text-center px-6">
            <div class="w-16 h-16 rounded-full bg-surface2 flex items-center justify-center text-accent mb-4">
              <PhChatsCircle :size="30" />
            </div>
            <h3 class="font-medium text-app mb-1">开始一场跨次元对话</h3>
            <p class="text-sm text-muted mb-5 max-w-sm">
              选择小说中的人物，向他们提问、闲聊或探讨剧情。支持多人同场群聊。
            </p>
            <Button @click="openCreate"><PhPlus :size="16" class="mr-1" />新建对话</Button>
          </div>
        </template>
        <template v-else>
          <!-- 头部角色卡 -->
          <div class="flex items-center gap-3 px-4 py-3 border-b border-app">
            <div class="flex -space-x-2">
              <Avatar
                v-for="ch in active.characters"
                :key="ch.id"
                :name="ch.name"
                size="md"
                class="ring-2 ring-surface"
              />
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-semibold text-app truncate">{{ active.characters.map((c) => c.name).join('、') }}</span>
                <Tag v-for="r in uniqueRoles" :key="r" :label="r" />
              </div>
              <div class="text-xs text-muted truncate">《{{ active.novel_name }}》 · {{ active.title }}</div>
            </div>
            <Button variant="ghost" size="sm" @click="openPersona">人设</Button>
          </div>

          <!-- 消息流 -->
          <div ref="scrollEl" class="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            <div v-if="messages.length === 0" class="h-full flex flex-col items-center justify-center text-center">
              <p class="text-sm text-muted mb-4">还没有消息，试试这些开场白：</p>
              <div class="flex flex-wrap gap-2 justify-center max-w-md">
                <button
                  v-for="q in starters"
                  :key="q"
                  @click="draft = q; send()"
                  class="px-3 py-1.5 text-sm rounded-full bg-surface2 text-app hover:border-accent border border-transparent transition"
                >{{ q }}</button>
              </div>
            </div>

            <div
              v-for="(m, i) in messages"
              :key="i"
              class="flex items-end gap-2"
              :class="m.role === 'user' ? 'flex-row-reverse' : ''"
            >
              <Avatar :name="m.role === 'user' ? '我' : (active.characters[0]?.name || '角')" size="sm" />
              <div class="max-w-[72%]">
                <div class="text-xs text-muted mb-1" :class="m.role === 'user' ? 'text-right' : ''">
                  {{ m.role === 'user' ? '我' : speakerName }}
                </div>
                <div
                  class="px-3.5 py-2.5 rounded-[var(--radius-md)] text-sm leading-relaxed whitespace-pre-wrap"
                  :class="m.role === 'user'
                    ? 'bg-accent text-white rounded-br-sm'
                    : 'bg-surface2 text-app rounded-bl-sm border-l-2 border-accent'"
                >
                  {{ m.content }}<span v-if="m.streaming" class="inline-block w-1.5 ml-0.5 animate-pulse">▍</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 输入 -->
          <div class="border-t border-app p-3">
            <div class="flex gap-2">
              <Input v-model="draft" placeholder="输入消息，回车发送…" class="flex-1" @keyup.enter="send" />
              <Button @click="send" :loading="sending">发送</Button>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 新建对话 Modal -->
    <Modal v-model="createOpen" title="新建对话">
      <div class="space-y-5">
        <div>
          <label class="text-sm text-muted">选择小说</label>
          <div class="mt-2 grid grid-cols-3 gap-2">
            <button
              v-for="n in novels"
              :key="n.id"
              @click="pickNovel(n.id)"
              class="px-3 py-2 text-sm rounded-[var(--radius-sm)] border transition truncate"
              :class="form.novel_id === n.id ? 'border-accent text-accent bg-surface2' : 'border-app text-app hover:border-accent'"
            >《{{ n.name }}》</button>
          </div>
        </div>

        <div v-if="form.novel_id">
          <label class="text-sm text-muted">选择人物（可多选，开启群聊）</label>
          <div class="mt-2 grid grid-cols-2 gap-2">
            <button
              v-for="ch in availableChars"
              :key="ch.id"
              @click="toggleChar(ch.id)"
              class="flex items-center gap-2 px-3 py-2 text-sm rounded-[var(--radius-sm)] border transition"
              :class="form.character_ids.includes(ch.id) ? 'border-accent bg-surface2' : 'border-app hover:border-accent'"
            >
              <Avatar :name="ch.name" size="sm" />
              <span class="text-app truncate flex-1 text-left">{{ ch.name }}</span>
              <Tag :label="ch.role" />
            </button>
          </div>
        </div>

        <div>
          <label class="text-sm text-muted">会话标题（可选）</label>
          <Input v-model="form.title" placeholder="留空则自动生成" class="mt-2 w-full" />
        </div>
      </div>
      <div class="flex justify-end gap-2 mt-6">
        <Button variant="ghost" @click="createOpen = false">取消</Button>
        <Button :disabled="!canCreate" @click="create">开始对话</Button>
      </div>
    </Modal>

    <!-- 人设 Drawer -->
    <Drawer v-model="showPersona" :title="active?.characters[0]?.name || '人设'">
      <div v-if="persona" class="space-y-4">
        <div class="flex items-center gap-3">
          <Avatar :name="persona.name" size="lg" />
          <div>
            <div class="font-semibold text-app">{{ persona.name }}</div>
            <Tag :label="persona.role" />
          </div>
        </div>
        <p class="text-sm text-app leading-relaxed">{{ persona.desc }}</p>
        <p class="text-xs text-muted">出场 {{ persona.appearances }} 次</p>
      </div>
      <div v-else class="text-sm text-muted">加载中…</div>
    </Drawer>

    <ConfirmDialog v-model="delOpen" title="删除会话" :message="`确定删除会话「${pending?.title}」？对话记录将一并清除。`" @confirm="doDelete" />
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { PhPlus, PhTrash, PhChatsCircle } from '@phosphor-icons/vue'
import Avatar from '@/components/ui/Avatar.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Tag from '@/components/ui/Tag.vue'
import Modal from '@/components/ui/Modal.vue'
import Drawer from '@/components/ui/Drawer.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { listConversations, getConversation, createConversation, deleteConversation, openChatSocket } from '@/api/conversations'
import { listNovels } from '@/api/novels'
import { listCharacters, getCharacter } from '@/api/characters'
import { useToast } from '@/composables/useToast'

const conversations = ref([])
const active = ref(null)
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const loading = ref(true)
const scrollEl = ref(null)

const novels = ref([])
const createOpen = ref(false)
const form = ref({ novel_id: null, character_ids: [], title: '' })
const availableChars = ref([])

const showPersona = ref(false)
const persona = ref(null)

const { notify } = useToast()
const delOpen = ref(false)
const pending = ref(null)

// WebSocket 连接管理（M7.5 流式输出）：每个会话维护一条长连接，后端逐 token 回传并渲染。
const socket = ref(null)

function closeSocket() {
  if (socket.value) {
    socket.value.close()
    socket.value = null
  }
}

function ensureSocket() {
  if (!active.value) return
  if (socket.value && socket.value.readyState === WebSocket.OPEN) return
  const token = localStorage.getItem('token')
  socket.value = openChatSocket(active.value.id, token)
  socket.value.onmessage = (ev) => {
    let data
    try { data = JSON.parse(ev.data) } catch { return }
    const last = messages.value[messages.value.length - 1]
    if (data.type === 'token') {
      if (last && last.role === 'assistant' && last.streaming) last.content += data.content
      scrollToBottom()
    } else if (data.type === 'done') {
      if (last) last.streaming = false
      sending.value = false
    } else if (data.type === 'error') {
      notify(data.content || '对话出错', 'error')
      if (last) { last.content += '\n[出错] ' + (data.content || ''); last.streaming = false }
      sending.value = false
    }
  }
}

onUnmounted(() => closeSocket())

const starters = [
  '聊聊你最近的经历',
  '你怎么看待自己的宿命？',
  '讲讲你印象最深的一件事',
  '如果重来一次，你会怎么做？'
]

const uniqueRoles = computed(() => [...new Set((active.value?.characters || []).map((c) => c.role))])
const speakerName = computed(() => (active.value?.characters || []).map((c) => c.name).join('、') || '角色')
const canCreate = computed(() => form.value.novel_id && form.value.character_ids.length > 0)

onMounted(async () => {
  const [convRes, novelRes] = await Promise.all([listConversations(), listNovels()])
  conversations.value = convRes.data || []
  novels.value = novelRes.data?.list || []
  loading.value = false
})

async function select(c) {
  closeSocket()
  active.value = c
  const res = await getConversation(c.id)
  messages.value = res.data?.messages || []
  scrollToBottom()
  ensureSocket()
}

function toggleChar(id) {
  const arr = form.value.character_ids
  const idx = arr.indexOf(id)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(id)
}

async function openCreate() {
  if (!novels.value.length) {
    const res = await listNovels()
    novels.value = res.data?.list || []
  }
  form.value = { novel_id: null, character_ids: [], title: '' }
  availableChars.value = []
  createOpen.value = true
}

async function pickNovel(id) {
  form.value.novel_id = id
  form.value.character_ids = []
  const res = await listCharacters(id)
  availableChars.value = res.data || []
}

async function create() {
  if (!canCreate.value) return
  const res = await createConversation({
    novel_id: form.value.novel_id,
    character_ids: [...form.value.character_ids],
    title: form.value.title || undefined
  })
  const conv = res.data
  conversations.value.unshift(conv)
  createOpen.value = false
  await select(conv)
}

async function remove(c) {
  pending.value = c
  delOpen.value = true
}

async function doDelete() {
  if (!pending.value) return
  await deleteConversation(pending.value.id)
  conversations.value = conversations.value.filter((x) => x.id !== pending.value.id)
  if (active.value?.id === pending.value.id) {
    active.value = null
    messages.value = []
  }
  notify(`已删除会话「${pending.value.title}」`, 'success')
  pending.value = null
}

async function openPersona() {
  if (!active.value?.characters?.length) return
  showPersona.value = true
  persona.value = null
  const res = await getCharacter(active.value.characters[0].id)
  persona.value = res.data || active.value.characters[0]
}

function scrollToBottom() {
  nextTick(() => {
    if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  })
}

function send() {
  if (!draft.value.trim() || !active.value || sending.value) return
  const text = draft.value.trim()
  ensureSocket()
  messages.value.push({ role: 'user', content: text })
  draft.value = ''
  sending.value = true
  scrollToBottom()
  const reply = { role: 'assistant', content: '', streaming: true }
  messages.value.push(reply)
  // 通过 WebSocket 发送消息，后端 RAG + 角色扮演后逐 token 回传（见 ensureSocket.onmessage）
  const payload = JSON.stringify({ type: 'message', content: text })
  const sock = socket.value
  if (sock.readyState === WebSocket.OPEN) {
    sock.send(payload)
  } else {
    sock.onopen = () => sock.send(payload)
  }
}
</script>
