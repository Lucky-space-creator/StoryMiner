<template>
  <div
    class="inline-flex items-center justify-center rounded-full font-medium text-white select-none shrink-0"
    :style="{ width: sizePx, height: sizePx, background: color, fontSize: fontPx }"
    :title="name"
  >
    <template v-if="count > 1">{{ count }}</template>
    <template v-else>{{ initial }}</template>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, default: '?' },
  size: { type: String, default: 'sm' }, // sm | md | lg
  count: { type: Number, default: 1 }
})

const palette = ['#0d9488', '#6366f1', '#db2777', '#d97706', '#0891b2', '#7c3aed', '#dc2626', '#16a34a']

const color = computed(() => {
  let h = 0
  for (const ch of props.name) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return palette[h % palette.length]
})

const initial = computed(() => (props.name || '?').slice(-1))

const sizePx = computed(() => ({ sm: '28px', md: '36px', lg: '56px' }[props.size] || '28px'))
const fontPx = computed(() => ({ sm: '12px', md: '14px', lg: '22px' }[props.size] || '12px'))
</script>
