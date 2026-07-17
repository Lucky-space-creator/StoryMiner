<template>
  <label class="block">
    <span v-if="label" class="block text-sm text-app mb-1.5">{{ label }}</span>
    <input
      :type="type"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
      :placeholder="placeholder"
      class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent transition"
    />
    <span v-if="error" class="block text-xs mt-1" style="color: var(--danger)">{{ error }}</span>
  </label>
</template>

<script setup>
defineProps({
  modelValue: String,
  label: String,
  type: { default: 'text' },
  placeholder: String,
  error: String
})
defineEmits(['update:modelValue'])
</script>
