<template>
  <!-- 关系编辑/新增弹窗：复用 Modal，含起点/终点选择、类型联想、出处/证据；编辑态显示删除 -->
  <Modal v-model="open" :title="isEdit ? '编辑关系' : '新增关系'">
    <div class="space-y-4">
      <div class="grid grid-cols-2 gap-3">
        <label class="block">
          <span class="block text-sm text-app mb-1.5">起点</span>
          <select
            v-model="form.source_id"
            class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app focus:outline-none focus:ring-2 ring-accent"
          >
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.name }}</option>
          </select>
        </label>
        <label class="block">
          <span class="block text-sm text-app mb-1.5">终点</span>
          <select
            v-model="form.target_id"
            class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app focus:outline-none focus:ring-2 ring-accent"
          >
            <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.name }}</option>
          </select>
        </label>
      </div>
      <label class="block">
        <span class="block text-sm text-app mb-1.5">关系类型</span>
        <input
          v-model="form.type"
          list="rel-types"
          placeholder="如：师徒 / 父子 / 敌对"
          class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent"
        />
        <datalist id="rel-types">
          <option v-for="t in relationTypes" :key="t.code" :value="t.code">{{ t.label }}</option>
        </datalist>
      </label>
      <label class="block">
        <span class="block text-sm text-app mb-1.5">出处 / 证据</span>
        <textarea
          v-model="form.evidence"
          rows="3"
          placeholder="该关系的出处或佐证文本"
          class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent"
        ></textarea>
      </label>
    </div>
    <div class="flex justify-between mt-6">
      <Button v-if="isEdit" variant="danger" @click="$emit('request-delete')">删除</Button>
      <span v-else></span>
      <div class="flex gap-2">
        <Button variant="ghost" @click="close">取消</Button>
        <Button variant="primary" @click="onSave">保存</Button>
      </div>
    </div>
  </Modal>
</template>

<script setup>
import { ref, watch, computed } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import Button from '@/components/ui/Button.vue'
import { createRelation, updateRelation } from '@/api/graph'
import { useToast } from '@/composables/useToast'

const props = defineProps({
  modelValue: Boolean,
  novelId: [Number, String],
  relation: { type: Object, default: null },
  nodes: { type: Array, default: () => [] },
  relationTypes: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:modelValue', 'saved', 'request-delete'])
const { notify } = useToast()
const open = ref(props.modelValue)
const form = ref({ source_id: null, target_id: null, type: '', evidence: '' })
const isEdit = computed(() => !!props.relation)

watch(() => props.modelValue, (v) => {
  open.value = v
  if (v) resetForm()
})
watch(open, (v) => emit('update:modelValue', v))

function resetForm() {
  const r = props.relation
  const ns = props.nodes || []
  form.value = {
    source_id: r && r.source != null ? r.source : (ns[0]?.id ?? null),
    target_id: r && r.target != null ? r.target : (ns[1]?.id ?? ns[0]?.id ?? null),
    type: (r && r.label) || '',
    evidence: (r && r.evidence) || ''
  }
}

function close() {
  open.value = false
}

async function onSave() {
  const payload = {
    source_id: Number(form.value.source_id),
    target_id: Number(form.value.target_id),
    type: form.value.type.trim(),
    evidence: form.value.evidence || null
  }
  if (!payload.source_id || !payload.target_id || !payload.type) {
    notify('请完整填写起点 / 终点 / 类型', 'error')
    return
  }
  try {
    if (isEdit.value) {
      await updateRelation(props.novelId, props.relation.id, payload)
      notify('关系已更新', 'success')
    } else {
      await createRelation(props.novelId, payload)
      notify('关系已创建', 'success')
    }
    open.value = false
    emit('saved')
  } catch (e) {
    notify(e?.message || '保存失败', 'error')
  }
}
</script>
