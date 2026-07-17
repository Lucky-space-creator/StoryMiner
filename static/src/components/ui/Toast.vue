<template>
  <Teleport to="body">
    <div class="fixed bottom-5 right-5 z-[60] flex flex-col gap-2 pointer-events-none">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="px-4 py-2.5 rounded-[var(--radius-md)] bg-surface border border-app shadow-lg text-sm text-app min-w-[200px] flex items-center gap-2 animate-[fadeIn_.2s_ease]"
      >
        <span class="w-2 h-2 rounded-full shrink-0" :style="{ background: color(t.type) }"></span>
        <span class="flex-1">{{ t.message }}</span>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { useToast } from '@/composables/useToast'
const { toasts } = useToast()
const color = (t) =>
  ({ success: 'var(--success)', error: 'var(--danger)', warning: 'var(--warning)', info: 'var(--accent)' }[t] || 'var(--accent)')
</script>

<style scoped>
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
