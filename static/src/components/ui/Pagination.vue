<template>
  <!-- 分页器：展示总数/页码，支持每页大小切换与上下翻页 -->
  <div class="flex items-center justify-between gap-4 mt-4">
    <span class="text-xs text-muted">共 {{ total }} 条 · 第 {{ page }}/{{ pages }} 页</span>
    <div class="flex items-center gap-2">
      <select
        :value="size"
        @change="$emit('update:size', Number($event.target.value))"
        class="px-2 py-1.5 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app text-sm"
      >
        <option v-for="s in pageSizes" :key="s" :value="s">{{ s }}/页</option>
      </select>
      <Button variant="secondary" size="sm" :disabled="page <= 1" @click="$emit('update:page', page - 1)">上一页</Button>
      <Button variant="secondary" size="sm" :disabled="page >= pages" @click="$emit('update:page', page + 1)">下一页</Button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import Button from './Button.vue'

// 整体思路：受控分页组件，页码/每页大小由父组件维护，仅向上 emit 变更事件。
// 关键点：pages 由 total/size 计算，越界时禁用翻页按钮。
// 实现逻辑：select 改变 emit update:size；上下页按钮 emit update:page。
const props = defineProps({
  page: { type: Number, default: 1 },
  size: { type: Number, default: 20 },
  total: { type: Number, default: 0 },
  pageSizes: { type: Array, default: () => [10, 20, 50] }
})
const emit = defineEmits(['update:page', 'update:size'])
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.size)))
</script>
