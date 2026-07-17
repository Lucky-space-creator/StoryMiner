import { ref } from 'vue'

// 全局共享的 toast 队列（模块级单例）
const toasts = ref([])
let seq = 0

export function useToast() {
  function notify(message, type = 'info', timeout = 2500) {
    const id = ++seq
    toasts.value.push({ id, message, type })
    setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, timeout)
    return id
  }
  return { toasts, notify }
}
