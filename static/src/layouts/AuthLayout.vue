<template>
  <div class="min-h-[100dvh] bg-app flex items-center justify-center px-4">
    <div class="w-full max-w-md">
      <div class="mb-8 text-center">
        <div class="inline-flex items-center gap-2 text-accent mb-3">
          <PhBookOpen :size="28" weight="duotone" />
          <span class="text-xl font-semibold tracking-tight text-app">小说解析 RAG</span>
        </div>
        <p class="text-muted text-sm">把小说变成可检索、可对话的知识库</p>
      </div>
      <div class="bg-surface border border-app rounded-[var(--radius-lg)] p-6 shadow-sm">
        <slot />
      </div>
    </div>
  </div>
</template>

<script setup>
import { PhBookOpen } from '@phosphor-icons/vue'
</script>
