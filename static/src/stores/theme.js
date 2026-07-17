import { defineStore } from 'pinia'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    isDark: false
  }),
  actions: {
    init() {
      const saved = localStorage.getItem('theme')
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      this.isDark = saved ? saved === 'dark' : prefersDark
      this.apply()
    },
    toggle() {
      this.isDark = !this.isDark
      localStorage.setItem('theme', this.isDark ? 'dark' : 'light')
      this.apply()
    },
    apply() {
      document.documentElement.classList.toggle('dark', this.isDark)
    }
  }
})
