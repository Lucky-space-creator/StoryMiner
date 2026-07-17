import { ref, onUnmounted } from 'vue'

// SSE 封装：用于解析进度(M1.11)、对话(M7)、续写(M8)的流式推送
export function useSSE(url, onMessage) {
  const es = ref(null)
  const connected = ref(false)
  const error = ref(null)

  function connect() {
    close()
    es.value = new EventSource(url)
    es.value.onopen = () => {
      connected.value = true
      error.value = null
    }
    es.value.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data))
      } catch {
        /* 忽略非 JSON 帧 */
      }
    }
    es.value.onerror = () => {
      connected.value = false
      error.value = '连接中断'
    }
  }

  function close() {
    if (es.value) {
      es.value.close()
      es.value = null
    }
    connected.value = false
  }

  onUnmounted(close)
  return { connect, close, connected, error }
}
