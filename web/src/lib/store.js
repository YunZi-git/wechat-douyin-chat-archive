import { reactive } from 'vue'
import { api } from './api'

export const store = reactive({
  view: 'chats',           // chats | merge | import | export | settings
  booted: false,
  stats: null,
  settings: {
    theme: 'dark',
    density: 'comfortable',
    page_size: 60,
  },
  toasts: [],
  // 跨页面跳转：合并页点「查看会话」时用它
  activeThreadId: null,
  focusSeq: 0,             // 每次强制聚焦自增，让聊天页重新加载
})

let toastSeed = 0

export function toast(message, type = '') {
  const id = ++toastSeed
  store.toasts.push({ id, message, type })
  setTimeout(() => {
    const i = store.toasts.findIndex((t) => t.id === id)
    if (i >= 0) store.toasts.splice(i, 1)
  }, type === 'err' ? 5200 : 2600)
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme)
  store.settings.theme = theme
  try {
    localStorage.setItem('chatwork.theme', theme)
  } catch {
    /* 隐私模式忽略 */
  }
}

export function applyDensity(density) {
  store.settings.density = density
  document.documentElement.setAttribute('data-density', density)
}

export async function refreshStats() {
  try {
    store.stats = await api.stats()
  } catch (err) {
    toast(`读取统计失败：${err.message}`, 'err')
  }
}

export async function boot() {
  const view = new URLSearchParams(location.search).get('view')
  if (['chats', 'merge', 'import', 'export', 'settings'].includes(view)) {
    store.view = view
  }
  try {
    const [settings] = await Promise.all([api.settings()])
    Object.assign(store.settings, settings)
    applyTheme(settings.theme || 'dark')
    applyDensity(settings.density || 'comfortable')
    await refreshStats()
  } catch (err) {
    toast(`初始化失败：${err.message}`, 'err')
  } finally {
    store.booted = true
  }
}

export function openThread(threadId) {
  store.activeThreadId = threadId
  store.focusSeq += 1
  store.view = 'chats'
}
