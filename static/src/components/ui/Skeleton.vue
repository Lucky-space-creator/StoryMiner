<template>
  <div class="animate-pulse bg-surface2 rounded-[var(--radius-sm)]" :style="{ height: h, width: w }"></div>
</template>

<script setup>
defineProps({
  h: { default: '1rem' },
  w: { default: '100%' }
})
</script>
