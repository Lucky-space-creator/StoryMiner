<template>
  <form @submit.prevent="submit" class="space-y-4">
    <h2 class="text-lg font-semibold text-app">{{ isRegister ? '注册账号' : '登录工作台' }}</h2>
    <Input v-model="form.username" label="用户名" placeholder="请输入用户名" />
    <Input v-model="form.password" label="密码" type="password" placeholder="请输入密码" />
    <Input v-if="isRegister" v-model="form.name" label="昵称" placeholder="可选" />
    <Button type="submit" :loading="loading" class="w-full">{{ isRegister ? '注册' : '登录' }}</Button>
    <p class="text-sm text-muted text-center">
      {{ isRegister ? '已有账号？' : '还没有账号？' }}
      <button type="button" @click="isRegister = !isRegister" class="text-accent hover:underline">
        {{ isRegister ? '去登录' : '去注册' }}
      </button>
    </p>
  </form>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useToast } from '@/composables/useToast'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'

const router = useRouter()
const user = useUserStore()
const { notify } = useToast()
const isRegister = ref(false)
const loading = ref(false)
const form = reactive({ username: '', password: '', name: '' })

// 整体思路：登录/注册提交后，无论成功失败都要给用户明确反馈。
// 关键点：http 拦截器已把后端 {code,msg} 归一化为 Error.message，
//   失败时必须显式 catch 并 notify，否则错误被静默吞掉（无弹窗）。
// 实现逻辑：try 内 await 登录/注册；catch 取 err.message 弹错误提示；
//   finally 复位 loading，避免按钮卡在加载态。
async function submit() {
  loading.value = true
  try {
    if (isRegister.value) await user.register({ ...form })
    else await user.login({ ...form })
    router.push('/dashboard')
  } catch (e) {
    notify(e?.message || '操作失败，请重试', 'error')
  } finally {
    loading.value = false
  }
}
</script>
