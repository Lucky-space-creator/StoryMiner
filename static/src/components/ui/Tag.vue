<template>
  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-surface2 text-muted border border-app">{{ label }}</span>
</template>

<script setup>
defineProps({ label: String })
</script>
