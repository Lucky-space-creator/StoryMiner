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
          <p class="text-xs text-muted mt-1">{{ t.stage }}</p>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { useTaskProgressStore } from '@/stores/taskProgress'
import { PhCaretDown } from '@phosphor-icons/vue'
const store = useTaskProgressStore()
</script>
