import { defineStore } from 'pinia'

// 解析任务进度（M1.11 / M14 悬浮小窗用），由 SSE 推送更新
export const useTaskProgressStore = defineStore('taskProgress', {
  state: () => ({
    tasks: [],
    visible: false
  }),
  actions: {
    upsert(task) {
      const idx = this.tasks.findIndex((t) => t.id === task.id)
      if (idx >= 0) this.tasks[idx] = { ...this.tasks[idx], ...task }
      else this.tasks.push(task)
    },
    show() { this.visible = true },
    hide() { this.visible = false },
    toggle() { this.visible = !this.visible }
  }
})
