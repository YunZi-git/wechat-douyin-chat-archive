// 后端 API 的薄封装：统一错误信息，避免每个组件都写 try/catch 样板。

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) {
    const detail = data?.detail || data?.error || `HTTP ${res.status}`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return data
}

const get = (path) => request(path)
const post = (path, body) =>
  request(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
const patch = (path, body) =>
  request(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) })

export const api = {
  health: () => get('/api/health'),
  stats: () => get('/api/stats'),

  threads: ({ platform, q, limit = 300, offset = 0 } = {}) => {
    const params = new URLSearchParams()
    if (platform) params.set('platform', platform)
    if (q) params.set('q', q)
    params.set('limit', limit)
    params.set('offset', offset)
    return get(`/api/threads?${params}`)
  },
  thread: (id) => get(`/api/threads/${encodeURIComponent(id)}`),
  messages: (id, { limit = 60, beforeTs, afterTs, aroundTs, platform } = {}) => {
    const params = new URLSearchParams({ limit })
    if (beforeTs) params.set('before_ts', beforeTs)
    if (afterTs) params.set('after_ts', afterTs)
    if (aroundTs) params.set('around_ts', aroundTs)
    if (platform) params.set('platform', platform)
    return get(`/api/threads/${encodeURIComponent(id)}/messages?${params}`)
  },
  days: (id, platform) => {
    const params = new URLSearchParams()
    if (platform) params.set('platform', platform)
    return get(`/api/threads/${encodeURIComponent(id)}/days?${params}`)
  },
  search: ({ q, platform, threadId, limit = 50 }) => {
    const params = new URLSearchParams({ q, limit })
    if (platform) params.set('platform', platform)
    if (threadId) params.set('thread_id', threadId)
    return get(`/api/search?${params}`)
  },

  contacts: ({ q, merged } = {}) => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (merged !== undefined && merged !== null) params.set('merged', merged)
    return get(`/api/contacts?${params}`)
  },
  merge: (primaryId, secondaryId, displayName) =>
    post('/api/contacts/merge', {
      primary_id: primaryId,
      secondary_id: secondaryId,
      display_name: displayName || null,
    }),
  unmerge: (contactId, platform) => post(`/api/contacts/${contactId}/unmerge`, { platform }),
  rename: (contactId, displayName, note) =>
    patch(`/api/contacts/${contactId}`, { display_name: displayName, note }),

  settings: () => get('/api/settings'),
  saveSettings: (body) => patch('/api/settings', body),

  sources: () => get('/api/sources'),
  wechatDecrypt: () => post('/api/wechat/decrypt'),
  wechatRefresh: () => post('/api/wechat/refresh'),
  importWechat: (accountDir) => post('/api/import/wechat', { account_dir: accountDir || null }),
  importDouyin: (body) => post('/api/import/douyin', body || {}),
  douyinCollect: (body) => post('/api/douyin/collect', body || {}),
  douyinRefresh: (body) => post('/api/douyin/refresh', body || {}),

  jobs: () => get('/api/jobs'),
  job: (id) => get(`/api/jobs/${id}`),
  cancelJob: (id) => post(`/api/jobs/${id}/cancel`),

  importVoiceJsonl: (path) => post('/api/voice/import-jsonl', { path }),

  exportThread: (threadId, format, platform) =>
    post('/api/export', { thread_id: threadId, format, platform: platform || null }),
  exports: () => get('/api/exports'),
}

export const downloadUrl = (name) => `/api/exports/${encodeURIComponent(name)}`
