import { defineStore } from 'pinia'

// 统一异步任务进度（悬浮小窗 / 仪表盘共用），由全局轮询 useTaskPoller 更新
export const useTaskProgressStore = defineStore('taskProgress', {
  state: () => ({
    tasks: [],
    visible: false
  }),
  actions: {
    // 新任务插入到列表头部（最新在上），已有任务原地更新；
    // 保证右下角「解析进度」悬浮窗按时间倒序展示（最近的在前）。
    upsert(task) {
      const idx = this.tasks.findIndex((t) => t.id === task.id)
      if (idx >= 0) this.tasks[idx] = { ...this.tasks[idx], ...task }
      else this.tasks.unshift(task)
    },
    remove(id) {
      this.tasks = this.tasks.filter((t) => t.id !== id)
    },
    show() { this.visible = true },
    hide() { this.visible = false },
    toggle() { this.visible = !this.visible }
  }
})
