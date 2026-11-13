import { reactive } from 'vue'
import { api } from './api'
import {
  BUILTIN_THEMES,
  applyTheme as applyThemeObject,
  findTheme,
  restoreThemeFromCache,
  systemTheme,
} from './themes'

export const store = reactive({
  view: 'chats',           // chats | voice | merge | import | export | appearance | settings
  booted: false,
  stats: null,
  settings: {
    theme: 'dark',
    density: 'comfortable',
    page_size: 60,
  },
  themes: [],              // 内置 4 个 + StyleKit 151 个
  themeSlug: 'dark',
  toasts: [],
  activeThreadId: null,
  focusSeq: 0,
  pendingJump: null,
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

export function currentTheme() {
  return findTheme(store.themes, store.themeSlug) || BUILTIN_THEMES[0]
}

/** 按 slug 应用外观（不写后端）。 */
export function applyTheme(slug) {
  if (slug === 'system') {
    const theme = systemTheme(store.themes)
    applyThemeObject(theme, 'system')
    store.themeSlug = 'system'
    store.settings.theme = 'system'
    return theme
  }
  const theme = findTheme(store.themes, slug) || BUILTIN_THEMES[0]
  applyThemeObject(theme)
  store.themeSlug = theme.slug
  store.settings.theme = theme.slug
  return theme
}

let systemListener = null

export function initSystemTheme() {
  if (systemListener || typeof window === 'undefined' || !window.matchMedia) return
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  systemListener = () => {
    if (store.settings.theme === 'system') applyTheme('system')
  }
  media.addEventListener?.('change', systemListener)
}

/** 切换外观并持久化到本机设置。 */
export async function setTheme(slug) {
  const theme = applyTheme(slug)
  try {
    await api.saveSettings({ theme: theme.slug })
  } catch (err) {
    toast(`保存外观失败：${err.message}`, 'err')
  }
  return theme
}

export function applyDensity(density) {
  store.settings.density = density
  document.documentElement.setAttribute('data-density', density)
}

export async function loadThemes() {
  try {
    const data = await api.themes()
    store.themes = [...BUILTIN_THEMES, ...(data.themes || [])]
  } catch (err) {
    store.themes = [...BUILTIN_THEMES]
    toast(`读取外观库失败：${err.message}`, 'err')
  }
  return store.themes
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
  if (['chats', 'search', 'voice', 'merge', 'import', 'export', 'appearance', 'settings'].includes(view)) {
    store.view = view
  }
  // 先把缓存的调色板贴上，避免首屏闪烁
  const cached = restoreThemeFromCache()
  try {
    await loadThemes()
    const settings = await api.settings()
    Object.assign(store.settings, settings)
    const initial = settings.theme === 'system'
      ? 'system'
      : (cached || settings.theme || 'dark')
    applyTheme(initial)
    initSystemTheme()
    applyDensity(settings.density || 'comfortable')
    await refreshStats()
  } catch (err) {
    applyTheme(cached || 'dark')
    toast(`初始化失败：${err.message}`, 'err')
  } finally {
    store.booted = true
  }
}

let jumpSeed = 0

export function openThread(threadId) {
  store.activeThreadId = threadId
  store.focusSeq += 1
  store.view = 'chats'
}

/** 打开会话并跳到某个时间点（搜索结果/日期跳转用）。 */
export function openThreadAt(threadId, ts, msgId) {
  store.activeThreadId = threadId
  store.pendingJump = { threadId, ts, msgId, seq: ++jumpSeed }
  store.view = 'chats'
}
