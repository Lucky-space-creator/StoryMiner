<template>
  <div class="flex flex-col items-center justify-center py-16 text-center">
    <div v-if="icon" class="w-12 h-12 rounded-[var(--radius-md)] bg-surface2 flex items-center justify-center text-muted mb-3">
      <component :is="icon" :size="22" />
    </div>
    <p class="text-sm text-app">{{ title }}</p>
    <p v-if="desc" class="text-xs text-muted mt-1">{{ desc }}</p>
    <div class="mt-4"><slot /></div>
  </div>
</template>

<script setup>
defineProps({ title: String, desc: String, icon: { default: null } })
</script>
