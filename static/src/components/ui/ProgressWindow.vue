<template>
  <Teleport to="body">
    <div
      v-if="store.visible && store.tasks.length"
      class="fixed bottom-4 right-4 z-40 w-80 bg-surface border border-app rounded-[var(--radius-lg)] shadow-xl overflow-hidden"
    >
      <div class="flex items-center justify-between px-4 py-3 border-b border-app">
        <span class="text-sm font-medium text-app">解析进度</span>
        <button @click="store.hide()" class="text-muted hover:text-app transition"><PhCaretDown :size="16" /></button>
      </div>
      <div class="max-h-72 overflow-y-auto p-3 space-y-3">
        <div v-for="t in store.tasks" :key="t.id" class="text-sm">
          <div class="flex justify-between text-app mb-1">
            <span class="truncate mr-2">{{ t.name }}</span>
            <span class="text-muted shrink-0">{{ t.progress }}%</span>
          </div>
          <div class="h-1.5 bg-surface2 rounded-full overflow-hidden">
            <div class="h-full bg-accent transition-all duration-300" :style="{ width: t.progress + '%' }"></div>
          </div>
          <p class="text-xs text-muted mt-1 flex items-center gap-2">
            <span class="px-1.5 py-0.5 rounded bg-surface2 text-accent">{{ typeText(t.type) }}</span>
            <span :class="t.status === 'failed' ? 'text-red-600' : (t.status === 'cancelled' ? 'text-stone-500' : '')">{{ stageText(t.stage) }}</span>
          </p>
          <p v-if="t.error" class="text-xs text-red-600 mt-1 break-words" :title="t.error">{{ t.error }}</p>
          <p v-if="t.tokens_in || t.tokens_out" class="text-xs text-muted mt-1">Token：{{ t.tokens_in }} 入 / {{ t.tokens_out }} 出</p>
          <div v-if="t.status === 'running'" class="mt-1 flex justify-end">
            <button class="text-xs text-red-600 hover:underline disabled:opacity-50" :disabled="cancelling[t.id]" @click="onCancel(t)">
              {{ cancelling[t.id] ? '取消中…' : '取消任务' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { reactive } from 'vue'
import { useTaskProgressStore } from '@/stores/taskProgress'
import { PhCaretDown } from '@phosphor-icons/vue'
import { typeText, stageText } from '@/utils/taskStages'
import { cancelTask } from '@/api/tasks'

const store = useTaskProgressStore()
const cancelling = reactive({})

async function onCancel(t) {
  cancelling[t.id] = true
  try {
    await cancelTask(t.id)
  } finally {
    cancelling[t.id] = false
  }
}
</script>
