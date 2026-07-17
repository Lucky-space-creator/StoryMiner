<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/40" @click="close"></div>
      <div class="relative bg-surface border border-app rounded-[var(--radius-lg)] shadow-lg w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div class="flex items-center justify-between px-5 py-4 border-b border-app">
          <h3 class="font-semibold text-app">{{ title }}</h3>
          <button @click="close" class="text-muted hover:text-app transition"><PhX :size="18" /></button>
        </div>
        <div class="p-5"><slot /></div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { PhX } from '@phosphor-icons/vue'
const props = defineProps({ modelValue: Boolean, title: String })
const emit = defineEmits(['update:modelValue'])
function close() { emit('update:modelValue', false) }
</script>
