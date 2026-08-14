<template>
  <div class="space-y-4">
    <h2 class="text-lg font-semibold text-app">续写与概览</h2>

    <div class="flex items-center gap-3">
      <label class="text-sm text-muted">小说</label>
      <select
        v-model="novelId"
        class="bg-surface border border-app rounded-[var(--radius-sm)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent"
        @change="onNovelChange"
      >
        <option v-for="n in novels" :key="n.id" :value="n.id">{{ n.name }}</option>
      </select>
    </div>

    <!-- 概览 / 时间线 / 角色弧线（M8.1/M8.2/M8.3） -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card>
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-app">情节概览</h3>
          <Button variant="secondary" :loading="summaryLoading" @click="runSummary">生成概览</Button>
        </div>
        <div v-if="summaryLoading" class="space-y-2">
          <Skeleton h="0.875rem" w="90%" />
          <Skeleton h="0.875rem" w="80%" />
          <Skeleton h="0.875rem" w="85%" />
        </div>
        <p v-else-if="summaryText" class="text-sm text-app whitespace-pre-wrap leading-relaxed">{{ summaryText }}</p>
        <p v-else class="text-sm text-muted">点击「生成概览」按章/卷梳理情节。</p>
      </Card>

      <Card>
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-app">时间线</h3>
          <Button variant="secondary" :loading="timelineLoading" @click="runTimeline">梳理时间线</Button>
        </div>
        <div v-if="timelineLoading" class="space-y-2">
          <Skeleton h="0.875rem" w="90%" />
          <Skeleton h="0.875rem" w="75%" />
          <Skeleton h="0.875rem" w="85%" />
        </div>
        <p v-else-if="timelineText" class="text-sm text-app whitespace-pre-wrap leading-relaxed">{{ timelineText }}</p>
        <p v-else class="text-sm text-muted">点击「梳理时间线」提取事件时间轴。</p>
      </Card>

      <Card>
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-app">角色弧线</h3>
          <Button variant="secondary" :loading="arcLoading" @click="runArc">生成弧线</Button>
        </div>
        <div v-if="arcLoading" class="space-y-2">
          <Skeleton h="0.875rem" w="90%" />
          <Skeleton h="0.875rem" w="80%" />
          <Skeleton h="0.875rem" w="85%" />
        </div>
        <p v-else-if="arcText" class="text-sm text-app whitespace-pre-wrap leading-relaxed">{{ arcText }}</p>
        <p v-else class="text-sm text-muted">点击「生成弧线」概览主要人物成长轨迹。</p>
      </Card>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- 续写（M8.4/M8.5 WebSocket 流式） -->
      <Card>
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-app">AI 续写</h3>
        </div>
        <textarea
          v-model="prompt"
          rows="3"
          placeholder="输入续写上下文或提示词…"
          class="w-full bg-surface2 border border-app rounded-[var(--radius-md)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent resize-none"
        ></textarea>
        <div class="flex flex-wrap gap-3 mt-3">
          <select v-model="style" class="bg-surface border border-app rounded-[var(--radius-sm)] px-2 py-1.5 text-sm text-app">
            <option value="original">贴合原风格</option>
            <option value="tense">紧张悬疑</option>
            <option value="warm">温情舒缓</option>
          </select>
          <select v-model="length" class="bg-surface border border-app rounded-[var(--radius-sm)] px-2 py-1.5 text-sm text-app">
            <option value="short">短（200字）</option>
            <option value="mid">中（500字）</option>
            <option value="long">长（1000字）</option>
          </select>
          <select v-model="pov" class="bg-surface border border-app rounded-[var(--radius-sm)] px-2 py-1.5 text-sm text-app">
            <option value="third">第三人称</option>
            <option value="first">第一人称</option>
          </select>
          <Button class="ml-auto" :loading="writing" @click="runWrite">生成续写</Button>
        </div>
        <div v-if="writing || writeText" class="mt-3 bg-surface2 border border-app rounded-[var(--radius-md)] p-3 text-sm text-app whitespace-pre-wrap leading-relaxed min-h-[6rem]">
          {{ writeText }}<span v-if="writing" class="animate-pulse">▍</span>
        </div>

        <!-- 用户编辑并保存到 MinIO（M8.9） -->
        <div v-if="writeText" class="mt-3 border-t border-app pt-3">
          <div class="flex items-center justify-between mb-2">
            <h4 class="text-sm font-medium text-app">编辑并保存到 MinIO</h4>
            <div class="flex items-center gap-2">
              <input
                v-model="saveName"
                placeholder="文件名（可选）"
                class="bg-surface border border-app rounded-[var(--radius-sm)] px-2 py-1 text-xs text-app outline-none focus:ring-2 ring-accent w-32"
              />
              <Button size="sm" :loading="saving" @click="saveContinue">保存到 MinIO</Button>
            </div>
          </div>
          <textarea
            v-model="editText"
            rows="6"
            placeholder="可在此修改续写内容后保存…"
            class="w-full bg-surface2 border border-app rounded-[var(--radius-md)] px-3 py-2 text-sm text-app outline-none focus:ring-2 ring-accent resize-y"
          ></textarea>
        </div>
      </Card>

      <!-- 续写版本（M8.6/M8.7） -->
      <Card>
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-app">续写版本</h3>
          <Button variant="secondary" :loading="versionLoading" @click="loadVersions">刷新</Button>
        </div>
        <p v-if="!versions.length" class="text-sm text-muted">暂无续写版本，生成续写后将自动保存于此。</p>
        <ul v-else class="space-y-2 max-h-72 overflow-auto">
          <li
            v-for="v in versions"
            :key="v.id"
            class="bg-surface2 border border-app rounded-[var(--radius-md)] p-3"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <p class="text-xs text-muted">
                  #{{ v.id }} · {{ styleLabel(v.style) }} · {{ lengthLabel(v.length) }} · {{ povLabel(v.perspective) }}
                </p>
                <p class="text-sm text-app mt-1 line-clamp-3">{{ v.preview }}</p>
              </div>
              <Button variant="secondary" size="sm" :loading="adopting === v.id" @click="adopt(v.id)">采纳为新章节</Button>
            </div>
          </li>
        </ul>
      </Card>

      <!-- 已保存续写（MinIO，M8.9） -->
      <Card>
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-app">已保存续写（MinIO）</h3>
          <Button variant="secondary" size="sm" :loading="savedLoading" @click="loadSaved">刷新</Button>
        </div>
        <p v-if="!savedList.length" class="text-sm text-muted">暂无保存到 MinIO 的续写草稿。</p>
        <ul v-else class="space-y-2 max-h-72 overflow-auto">
          <li
            v-for="s in savedList"
            :key="s.object_key"
            class="bg-surface2 border border-app rounded-[var(--radius-md)] p-3"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <p class="text-sm text-app font-medium truncate">{{ s.name }}</p>
                <p class="text-xs text-muted">
                  {{ s.size }} 字节 · {{ s.last_modified || '未知时间' }}
                </p>
              </div>
              <Button variant="secondary" size="sm" @click="openSaved(s)">查看/载入</Button>
            </div>
          </li>
        </ul>
      </Card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import {
  generateSummary,
  generateTimeline,
  generateCharacterArc,
  listVersions,
  adoptVersion,
  openWriteSocket,
  saveContinueWrite,
  listSavedContinues,
  readSavedContinue,
} from '@/api/writing'
import { listNovels } from '@/api/novels'

const novels = ref([])
const novelId = ref(1)

// 概览 / 时间线 / 角色弧线
const summaryLoading = ref(false)
const summaryText = ref('')
const timelineLoading = ref(false)
const timelineText = ref('')
const arcLoading = ref(false)
const arcText = ref('')

// 续写
const prompt = ref('')
const style = ref('original')
const length = ref('mid')
const pov = ref('third')
const writing = ref(false)
const writeText = ref('')
const writeSocket = ref(null)

// 版本
const versions = ref([])
const versionLoading = ref(false)
const adopting = ref(0)

// 编辑保存（M8.9）
const editText = ref('')
const saveName = ref('')
const saving = ref(false)
const savedList = ref([])
const savedLoading = ref(false)

function styleLabel(s) {
  return { original: '原风格', tense: '悬疑', warm: '温情' }[s] || s
}
function lengthLabel(l) {
  return { short: '短', mid: '中', long: '长' }[l] || l
}
function povLabel(p) {
  return { third: '第三人称', first: '第一人称' }[p] || p
}

onMounted(async () => {
  const res = await listNovels()
  novels.value = res.data?.list || []
  if (novels.value[0]) novelId.value = novels.value[0].id
  await loadVersions()
  await loadSaved()
})

onUnmounted(() => closeWriteSocket())

function closeWriteSocket() {
  if (writeSocket.value) {
    writeSocket.value.close()
    writeSocket.value = null
  }
}

async function runSummary() {
  summaryLoading.value = true
  try {
    const res = await generateSummary(novelId.value)
    summaryText.value = res.data?.text || ''
  } finally {
    summaryLoading.value = false
  }
}

async function runTimeline() {
  timelineLoading.value = true
  try {
    const res = await generateTimeline(novelId.value)
    timelineText.value = res.data?.text || ''
  } finally {
    timelineLoading.value = false
  }
}

async function runArc() {
  arcLoading.value = true
  try {
    const res = await generateCharacterArc(novelId.value)
    arcText.value = res.data?.text || ''
  } finally {
    arcLoading.value = false
  }
}

async function onNovelChange() {
  summaryText.value = ''
  timelineText.value = ''
  arcText.value = ''
  writeText.value = ''
  editText.value = ''
  await loadVersions()
  await loadSaved()
}

function runWrite() {
  if (!prompt.value.trim() || writing.value) return
  closeWriteSocket()
  writing.value = true
  writeText.value = ''

  const token = localStorage.getItem('token')
  const sock = openWriteSocket(novelId.value, token)
  writeSocket.value = sock

  sock.onmessage = async (ev) => {
    let data
    try { data = JSON.parse(ev.data) } catch { return }
    if (data.type === 'token') {
      writeText.value += data.content
    } else if (data.type === 'done') {
      writing.value = false
      editText.value = writeText.value
      await loadVersions()
    } else if (data.type === 'error') {
      writeText.value += '\n[出错] ' + (data.content || '')
      writing.value = false
    }
  }
  sock.onopen = () => {
    sock.send(JSON.stringify({
      type: 'continue',
      chapter_id: null,
      prompt: prompt.value,
      style: style.value,
      length: length.value,
      perspective: pov.value,
    }))
  }
}

async function loadVersions() {
  versionLoading.value = true
  try {
    const res = await listVersions(novelId.value)
    versions.value = res.data || []
  } finally {
    versionLoading.value = false
  }
}

async function adopt(versionId) {
  adopting.value = versionId
  try {
    await adoptVersion(novelId.value, versionId)
    await loadVersions()
  } finally {
    adopting.value = 0
  }
}

// 编辑后保存到 MinIO（M8.9）
async function saveContinue() {
  if (!editText.value.trim() || saving.value) return
  saving.value = true
  try {
    const res = await saveContinueWrite(novelId.value, editText.value, saveName.value || null)
    await loadSaved()
    alert('已保存到 MinIO：' + (res.data?.name || saveName.value))
  } catch (e) {
    alert('保存失败：' + (e?.response?.data?.msg || e.message))
  } finally {
    saving.value = false
  }
}

// 加载已保存的续写草稿列表
async function loadSaved() {
  savedLoading.value = true
  try {
    const res = await listSavedContinues(novelId.value)
    savedList.value = res.data?.items || []
  } finally {
    savedLoading.value = false
  }
}

// 查看/载入某份已保存草稿
async function openSaved(item) {
  try {
    const res = await readSavedContinue(novelId.value, item.object_key)
    editText.value = res.data?.content || ''
    saveName.value = item.name
    alert('已载入：' + item.name)
  } catch (e) {
    alert('读取失败：' + (e?.response?.data?.msg || e.message))
  }
}
</script>
