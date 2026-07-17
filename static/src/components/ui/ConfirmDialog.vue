<template>
  <Modal v-model="open" :title="title">
    <p class="text-sm text-app leading-relaxed">{{ message }}</p>
    <div class="flex justify-end gap-2 mt-6">
      <Button variant="ghost" @click="cancel">取消</Button>
      <Button :variant="danger ? 'danger' : 'primary'" @click="ok">{{ confirmText }}</Button>
    </div>
  </Modal>
</template>

<script setup>
import { ref, watch } from 'vue'
import Modal from './Modal.vue'
import Button from './Button.vue'

const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: '确认操作' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  danger: { type: Boolean, default: true }
})
const emit = defineEmits(['update:modelValue', 'confirm'])

const open = ref(props.modelValue)
watch(() => props.modelValue, (v) => (open.value = v))
watch(open, (v) => emit('update:modelValue', v))

function cancel() {
  open.value = false
}
function ok() {
  open.value = false
  emit('confirm')
}
</script>
