// 时间 / 数量 / 类型 的展示格式化。

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function fmtDay(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${WEEKDAYS[d.getDay()]}`
}

export function fmtDateTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate()
  ).padStart(2, '0')} ${fmtTime(ts)}`
}

export function fmtRelative(ts) {
  if (!ts) return ''
  const now = new Date()
  const d = new Date(ts * 1000)
  const sameDay =
    now.getFullYear() === d.getFullYear() &&
    now.getMonth() === d.getMonth() &&
    now.getDate() === d.getDate()
  if (sameDay) return fmtTime(ts)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (
    yesterday.getFullYear() === d.getFullYear() &&
    yesterday.getMonth() === d.getMonth() &&
    yesterday.getDate() === d.getDate()
  ) {
    return '昨天'
  }
  if (now.getFullYear() === d.getFullYear()) {
    return `${d.getMonth() + 1}/${d.getDate()}`
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

export function fmtCount(n) {
  const num = Number(n) || 0
  if (num >= 10000) {
    const w = num / 10000
    return `${w >= 10 ? Math.round(w) : w.toFixed(1).replace(/\.0$/, '')}万`
  }
  return String(num)
}

export const PLATFORM_META = {
  wechat: { label: '微信', short: '微', cls: 'wechat' },
  douyin: { label: '抖音', short: '抖', cls: 'douyin' },
}

export function platformLabel(p) {
  return PLATFORM_META[p]?.label || p || '未知'
}

const KIND_LABELS = {
  text: '文本',
  image: '[图片]',
  voice: '[语音]',
  video: '[视频]',
  sticker: '[表情]',
  share: '[分享]',
  card: '[名片]',
  location: '[位置]',
  call: '[通话]',
  system: '[系统消息]',
  revoke: '[撤回了一条消息]',
  forward: '[合并转发]',
  file: '[文件]',
  miniprogram: '[小程序]',
  channel: '[视频号]',
  link: '[链接]',
  other: '[消息]',
}

export function previewText(item) {
  const kind = item.preview_kind || 'text'
  const text = (item.preview || '').replace(/\s+/g, ' ').trim()
  if (kind === 'text') return text || '（暂无消息）'
  return text && !text.startsWith('[') ? text : KIND_LABELS[kind] || text || '（暂无消息）'
}

export function kindLabel(kind) {
  return KIND_LABELS[kind] || ''
}

export function initial(name) {
  const s = (name || '').trim()
  if (!s) return '?'
  // 中文取第一个字，英文取首字母
  return /[\u4e00-\u9fa5]/.test(s[0]) ? s[0] : s[0].toUpperCase()
}

export function avatarColor(seed) {
  const palette = [
    '#4f6bff', '#2fbf71', '#ff5f7e', '#f0a34a', '#9d7bff',
    '#31b0c8', '#e0653f', '#6a8f3a', '#c04fb0', '#3f8fbf',
  ]
  let hash = 0
  const s = String(seed || '')
  for (let i = 0; i < s.length; i += 1) hash = (hash * 31 + s.charCodeAt(i)) >>> 0
  return palette[hash % palette.length]
}

export function fmtBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}
