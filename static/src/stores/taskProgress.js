import { defineStore } from 'pinia'

// 统一异步任务进度（悬浮小窗 / 仪表盘共用），由全局轮询 useTaskPoller 更新
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
    remove(id) {
      this.tasks = this.tasks.filter((t) => t.id !== id)
    },
    show() { this.visible = true },
    hide() { this.visible = false },
    toggle() { this.visible = !this.visible }
  }
})
