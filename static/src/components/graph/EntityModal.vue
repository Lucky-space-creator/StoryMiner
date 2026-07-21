<template>
  <!-- 实体编辑/新增弹窗：复用 Modal，表单含名称/类型/画像(JSON)/描述；编辑态显示删除 -->
  <Modal v-model="open" :title="isEdit ? '编辑实体' : '新增实体'">
    <div class="space-y-4">
      <Input v-model="form.name" label="名称" placeholder="实体名称" :error="errors.name" />
      <label class="block">
        <span class="block text-sm text-app mb-1.5">类型</span>
        <select
          v-model="form.type"
          class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app focus:outline-none focus:ring-2 ring-accent"
        >
          <option v-for="et in entityTypes" :key="et.code" :value="et.code">{{ et.label }}</option>
        </select>
      </label>
      <label class="block">
        <span class="block text-sm text-app mb-1.5">画像（JSON，可选）</span>
        <textarea
          v-model="form.profile"
          rows="3"
          placeholder='{"身份":"主角"}'
          class="w-full px-3 py-2 rounded-[var(--radius-sm)] bg-surface2 border border-app text-app placeholder:text-muted focus:outline-none focus:ring-2 ring-accent font-mono text-xs"
        ></textarea>
        <span v-if="errors.profile" class="block text-xs mt-1" style="color: var(--danger)">{{ errors.profile }}</span>
      </label>
      <label class="block">
        <span class="block text-sm text-app mb-1.5">描述（可选）</span>
        <textarea
          v-model="form.description"
          rows="3"
          placeholder="实体描述"
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
import { ref, watch, computed, onMounted } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import { createEntity, updateEntity, getEntityTypes } from '@/api/graph'
import { useToast } from '@/composables/useToast'

const props = defineProps({
  modelValue: Boolean,
  novelId: [Number, String],
  entity: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue', 'saved', 'request-delete'])
const { notify } = useToast()
const open = ref(props.modelValue)
const errors = ref({})
const form = ref({ name: '', type: 'character', profile: '', description: '' })
const isEdit = computed(() => !!props.entity)
// V13: 7种实体类型从后端字典加载
const entityTypes = ref([
  { code: 'character', label: '人物' },
  { code: 'place', label: '地点' },
  { code: 'org', label: '组织' },
  { code: 'time_period', label: '时间' },
  { code: 'event', label: '事件' },
  { code: 'item', label: '物品' },
  { code: 'concept', label: '概念' },
])

onMounted(async () => {
  // 从后端加载实体类型字典（失败则使用默认值）
  try {
    const res = await getEntityTypes()
    if (res.data?.length) entityTypes.value = res.data
  } catch { /* 使用默认值 */ }
})

watch(() => props.modelValue, (v) => {
  open.value = v
  if (v) resetForm()
})
watch(open, (v) => emit('update:modelValue', v))

function resetForm() {
  errors.value = {}
  const e = props.entity || {}
  form.value = {
    name: e.name || '',
    type: e.type || 'character',
    profile: e.profile ? JSON.stringify(e.profile, null, 2) : '',
    description: e.description || ''
  }
}

function close() {
  open.value = false
}

async function onSave() {
  errors.value = {}
  if (!form.value.name.trim()) {
    errors.value.name = '名称不能为空'
    return
  }
  let profileObj = {}
  if (form.value.profile.trim()) {
    try {
      profileObj = JSON.parse(form.value.profile)
    } catch {
      errors.value.profile = '画像需为合法 JSON'
      return
    }
  }
  const payload = {
    name: form.value.name.trim(),
    type: form.value.type,
    profile: profileObj,
    description: form.value.description || null
  }
  try {
    if (isEdit.value) {
      await updateEntity(props.novelId, props.entity.id, payload)
      notify('实体已更新', 'success')
    } else {
      await createEntity(props.novelId, payload)
      notify('实体已创建', 'success')
    }
    open.value = false
    emit('saved')
  } catch (e) {
    notify(e?.message || '保存失败', 'error')
  }
}
</script>
