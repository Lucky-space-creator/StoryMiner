<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-50">
      <div class="absolute inset-0 bg-black/30" @click="close"></div>
      <div class="absolute right-0 top-0 h-full w-full max-w-md bg-surface border-l border-app shadow-xl flex flex-col">
        <div class="flex items-center justify-between px-5 py-4 border-b border-app">
          <h3 class="font-semibold text-app">{{ title }}</h3>
          <button @click="close" class="text-muted hover:text-app transition"><PhX :size="18" /></button>
        </div>
        <div class="p-5 flex-1 overflow-y-auto"><slot /></div>
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
