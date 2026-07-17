<template>
  <button
    :disabled="disabled || loading"
    :class="[
      'inline-flex items-center justify-center gap-2 rounded-[var(--radius-sm)] text-sm font-medium transition active:translate-y-px disabled:opacity-50 disabled:pointer-events-none',
      size === 'sm' ? 'px-3 py-1.5' : 'px-4 py-2',
      variant === 'primary' ? 'bg-accent text-white hover:bg-accent-hover' : '',
      variant === 'secondary' ? 'bg-surface2 text-app hover:bg-app border border-app' : '',
      variant === 'ghost' ? 'text-muted hover:bg-surface2 hover:text-app' : '',
      variant === 'danger' ? 'bg-[var(--danger)] text-white hover:opacity-90' : ''
    ]"
  >
    <span v-if="loading" class="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin"></span>
    <slot />
  </button>
</template>

<script setup>
defineProps({
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' },
  loading: Boolean,
  disabled: Boolean
})
</script>
