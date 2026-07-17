<template>
  <div class="flex flex-col items-center justify-center py-24 text-center">
    <div class="w-14 h-14 rounded-[var(--radius-md)] bg-surface2 flex items-center justify-center text-accent mb-4">
      <PhHammer :size="26" weight="duotone" />
    </div>
    <h2 class="text-lg font-semibold text-app">{{ route.meta.title || '页面' }}</h2>
    <p class="text-sm text-muted mt-2">该模块建设中，接口已按《API 列表》预留。</p>
    <RouterLink
      to="/dashboard"
      class="mt-6 inline-flex items-center gap-1 px-4 py-2 rounded-[var(--radius-sm)] bg-accent text-white text-sm hover:bg-accent-hover transition active:translate-y-px"
    >
      返回仪表盘
    </RouterLink>
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'
import { PhHammer } from '@phosphor-icons/vue'
const route = useRoute()
</script>
