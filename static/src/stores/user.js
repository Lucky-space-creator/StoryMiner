import { defineStore } from 'pinia'
import { login as apiLogin, register as apiRegister, me as apiMe } from '@/api/auth'

// 用户状态：登录/注册/当前用户，全部走真实后端。
// 整体思路：
//   登录/注册拿到 access_token 后存入 localStorage，再调用 /auth/me 获取用户资料。
// 关键点：
//   1. token 持久化，刷新后自动恢复登录态。
//   2. 登录成功后立即拉取用户，保证菜单与鉴权信息完整。
// 实现逻辑：
//   调用 api/auth 的真实请求；失败由 http 拦截器统一抛出，由视图捕获提示。
export const useUserStore = defineStore('user', {
  state: () => ({
    user: null,
    token: localStorage.getItem('token') || ''
  }),
  getters: {
    isLoggedIn: (s) => !!s.token
  },
  actions: {
    async login(payload) {
      const res = await apiLogin(payload)
      this.token = res.data.access_token
      localStorage.setItem('token', this.token)
      await this.fetchMe()
    },
    async register(payload) {
      const res = await apiRegister(payload)
      this.token = res.data.access_token
      localStorage.setItem('token', this.token)
      await this.fetchMe()
    },
    async fetchMe() {
      const res = await apiMe()
      this.user = res.data
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
    }
  }
})
