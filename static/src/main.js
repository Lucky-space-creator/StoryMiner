import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './styles/tailwind.css'
import { useToast } from '@/composables/useToast'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 全局兜底：捕获未处理的 Promise 拒绝（如 M2-M14 后端未就绪导致的请求失败），
// 以 toast 提示而非白屏崩溃。
const { notify } = useToast()
window.addEventListener('unhandledrejection', (e) => {
  e.preventDefault()
  const msg = e.reason?.message || '请求失败'
  notify(msg, 'error')
})
app.config.errorHandler = (err) => {
  notify(err?.message || '页面出错', 'error')
}

app.mount('#app')
