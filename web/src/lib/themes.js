// 外观主题引擎：把主题对象映射成 CSS 变量，完全自己实现，不依赖任何 UI 库。
//
// 主题分两类：
//   * 内置 4 个（dark/light/warm/purple）—— 沿用项目原来的配色
//   * StyleKit 的 151 个 —— 由 scripts/fetch_stylekit_themes.py 归一化生成

export const BUILTIN_THEMES = [
  {
    slug: 'dark',
    name: '深色（内置）',
    nameEn: 'Dark',
    category: 'builtin',
    description: '项目默认的深色外观',
    dark: true,
    source: 'builtin',
    shape: { radius: 12, borderWidth: 1 },
    palette: {
      bg: '#0d0f14', bgRail: '#0a0c10',
      panel: '#141822', panel2: '#1a1f2b', panel3: '#212736',
      border: '#242a39', borderSoft: '#1b202c', borderStrong: '#242a39',
      text: '#e8ebf3', textDim: '#9aa3b7', textFaint: '#69718a',
      accent: '#6c8cff', accentSoft: 'rgba(108, 140, 255, 0.14)',
      shadow: '0 18px 48px rgba(0, 0, 0, 0.45)',
    },
  },
  {
    slug: 'light',
    name: '浅色（内置）',
    nameEn: 'Light',
    category: 'builtin',
    description: '项目原来的浅色外观',
    dark: false,
    source: 'builtin',
    shape: { radius: 12, borderWidth: 1 },
    palette: {
      bg: '#f2f4f8', bgRail: '#e9ecf3',
      panel: '#ffffff', panel2: '#f6f8fc', panel3: '#eef1f7',
      border: '#dfe4ee', borderSoft: '#e9edf4', borderStrong: '#dfe4ee',
      text: '#1d2433', textDim: '#5c6577', textFaint: '#8b93a5',
      accent: '#3f6bff', accentSoft: 'rgba(63, 107, 255, 0.10)',
      shadow: '0 18px 48px rgba(23, 32, 55, 0.16)',
    },
  },
  {
    slug: 'warm',
    name: '暖棕（内置）',
    nameEn: 'Warm',
    category: 'builtin',
    description: '偏暖的深色外观',
    dark: true,
    source: 'builtin',
    shape: { radius: 12, borderWidth: 1 },
    palette: {
      bg: '#17120e', bgRail: '#120e0b',
      panel: '#211a15', panel2: '#2a211a', panel3: '#33281f',
      border: '#3a2d23', borderSoft: '#2c221a', borderStrong: '#3a2d23',
      text: '#f3e9de', textDim: '#bda893', textFaint: '#8a7561',
      accent: '#e0a25c', accentSoft: 'rgba(224, 162, 92, 0.15)',
      shadow: '0 18px 48px rgba(0, 0, 0, 0.5)',
    },
  },
  {
    slug: 'purple',
    name: '紫夜（内置）',
    nameEn: 'Purple',
    category: 'builtin',
    description: '偏紫的深色外观',
    dark: true,
    source: 'builtin',
    shape: { radius: 12, borderWidth: 1 },
    palette: {
      bg: '#100d1a', bgRail: '#0c0a14',
      panel: '#191531', panel2: '#221c3d', panel3: '#2b2449',
      border: '#322a55', borderSoft: '#241e3f', borderStrong: '#322a55',
      text: '#ece9fb', textDim: '#a79ecb', textFaint: '#7a71a0',
      accent: '#9d7bff', accentSoft: 'rgba(157, 123, 255, 0.16)',
      shadow: '0 18px 48px rgba(0, 0, 0, 0.5)',
    },
  },
]

export const CATEGORY_LABELS = {
  builtin: '内置',
  modern: '现代',
  minimal: '极简',
  retro: '复古',
  expressive: '表现力',
}

export function findTheme(themes, slug) {
  return themes.find((t) => t.slug === slug)
}

const SYSTEM_QUERY = '(prefers-color-scheme: dark)'

export function systemPrefersDark() {
  return typeof window !== 'undefined' && !!window.matchMedia
    && window.matchMedia(SYSTEM_QUERY).matches
}

export function systemTheme(themes) {
  return findTheme(themes, systemPrefersDark() ? 'dark' : 'light') || BUILTIN_THEMES[0]
}

function darker(hex, amount) {
  // 仅用于 --bg-rail 的兜底，主题数据一般已经带了 bgRail
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '')
  if (!m) return hex
  const n = parseInt(m[1], 16)
  const r = Math.max(0, ((n >> 16) & 255) - amount)
  const g = Math.max(0, ((n >> 8) & 255) - amount)
  const b = Math.max(0, (n & 255) - amount)
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`
}

function _luminance(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '')
  if (!m) return 0
  const n = parseInt(m[1], 16)
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const x = v / 255
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}

function onAccent(hex, fallback = '#ffffff') {
  if (!/^#[0-9a-f]{6}$/i.test(hex || '')) return fallback
  return _luminance(hex) > 0.56 ? '#0b0d12' : '#ffffff'
}

export function cssVars(theme) {
  const p = theme.palette || {}
  const shape = theme.shape || {}
  const radius = Number.isFinite(shape.radius) ? shape.radius : 12
  const rail = p.bgRail || darker(p.bg, theme.dark ? 6 : 10)
  const strong = p.borderStrong || p.border
  return {
    '--bg': p.bg,
    '--bg-rail': rail,
    '--panel': p.panel,
    '--panel-2': p.panel2,
    '--panel-3': p.panel3,
    '--border': p.border,
    '--border-soft': p.borderSoft,
    '--border-strong': strong,
    '--text': p.text,
    '--text-dim': p.textDim,
    '--text-faint': p.textFaint,
    '--accent': p.accent,
    '--accent-soft': p.accentSoft,
    '--shadow': p.shadow || 'none',
    '--shadow-sm': (p.shadow && p.shadow !== 'none')
      ? (theme.dark ? '0 6px 18px rgba(0, 0, 0, 0.26)' : '0 6px 18px rgba(23, 32, 55, 0.10)')
      : 'none',
    // 内置主题保持原来的扁平按钮；StyleKit 主题把自身阴影用到控件上
    '--shadow-control': (theme.source !== 'builtin' && p.shadow && p.shadow !== 'none')
      ? p.shadow : 'none',
    '--on-accent': onAccent(p.accent),
    '--radius-lg': `${radius > 0 ? radius + 4 : 0}px`,
    '--radius': `${radius}px`,
    '--radius-sm': `${Math.max(0, radius - 4)}px`,
    '--radius-pill': radius > 0 ? '999px' : '0px',
    '--radius-bubble': `${radius === 0 ? 0 : Math.min(18, radius + 2)}px`,
    '--border-w': `${shape.borderWidth ?? 1}px`,
  }
}

function scheduleNativeTitlebar(dark) {
  // pywebview 可能先创建隐藏窗口、再重建主窗口；窗口句柄会变，重试几次
  for (const delay of [0, 300, 900, 1800, 3500]) {
    setTimeout(() => syncNativeTitlebar(dark), delay)
  }
}

function syncNativeTitlebar(dark) {
  const css = getComputedStyle(document.documentElement)
  const bg = css.getPropertyValue('--bg').trim()
  const text = css.getPropertyValue('--text').trim()
  if (!bg || !text) return
  fetch('/api/titlebar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bg, text, dark: !!dark }),
  }).catch(() => {
    /* 浏览器预览/服务未启动时忽略 */
  })
}

export function applyTheme(theme, cacheSlug) {
  const root = document.documentElement
  const vars = cssVars(theme)
  for (const [key, value] of Object.entries(vars)) {
    if (value) root.style.setProperty(key, value)
  }
  root.setAttribute('data-theme', 'custom')
  root.style.colorScheme = theme.dark ? 'dark' : 'light'
  scheduleNativeTitlebar(theme.dark)
  try {
    localStorage.setItem('chatwork.theme', cacheSlug || theme.slug)
    localStorage.setItem('chatwork.palette', JSON.stringify(theme))
  } catch {
    /* 隐私模式忽略 */
  }
}

// 首屏防闪：index.html 里也有一份等价逻辑，这里给运行时用
export function restoreThemeFromCache() {
  try {
    const raw = localStorage.getItem('chatwork.palette')
    if (!raw) return null
    const theme = JSON.parse(raw)
    if (!theme?.palette) return null
    applyTheme(theme)
    return theme.slug
  } catch {
    return null
  }
}


if (typeof window !== 'undefined') {
  window.addEventListener('pywebviewready', () => {
    const raw = document.documentElement.style.getPropertyValue('--bg').trim()
    const dark = document.documentElement.style.colorScheme === 'dark'
    if (raw) scheduleNativeTitlebar(dark)
  })
}
