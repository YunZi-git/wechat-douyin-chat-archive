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
  accounts: () => get('/api/accounts'),

  threads: ({ platform, account, q, limit = 300, offset = 0 } = {}) => {
    const params = new URLSearchParams()
    if (platform) params.set('platform', platform)
    if (account) params.set('account', account)
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
  threadMedia: (id, { kind, platform, limit = 60, offset = 0 } = {}) => {
    const params = new URLSearchParams({ limit, offset })
    if (kind) params.set('kind', kind)
    if (platform) params.set('platform', platform)
    return get(`/api/threads/${encodeURIComponent(id)}/media?${params}`)
  },
  mediaPrewarm: (body) => post('/api/media/prewarm', body),
  days: (id, platform) => {
    const params = new URLSearchParams()
    if (platform) params.set('platform', platform)
    return get(`/api/threads/${encodeURIComponent(id)}/days?${params}`)
  },
  search: ({
    q, platform, threadId, sender, kinds, sinceTs, untilTs,
    limit = 50, offset = 0, group = false,
  }) => {
    const params = new URLSearchParams({ q, limit, offset })
    if (platform) params.set('platform', platform)
    if (threadId) params.set('thread_id', threadId)
    if (sender) params.set('sender', sender)
    if (kinds) params.set('kinds', kinds)
    if (sinceTs) params.set('since_ts', sinceTs)
    if (untilTs) params.set('until_ts', untilTs)
    if (group) params.set('group', 'true')
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

  themes: () => get('/api/themes'),
  version: () => get('/api/version'),
  selfIdentity: () => get('/api/self-identity'),
  openExternal: (url) => post('/api/open-external', { url }),
  setSelfIdentity: (body) => patch('/api/self-identity', body),
  autosyncStatus: () => get('/api/autosync/status'),
  autosyncRun: () => post('/api/autosync/run'),
  updateCheck: (url) => get('/api/update/check' + (url ? `?url=${encodeURIComponent(url)}` : '')),
  settings: () => get('/api/settings'),
  saveSettings: (body) => patch('/api/settings', body),

  sources: () => get('/api/sources'),
  wechatDecrypt: () => post('/api/wechat/decrypt'),
  wechatRefresh: (body) => post('/api/wechat/refresh', body || {}),
  importWechat: (accountDir, incremental = false) =>
    post('/api/import/wechat', { account_dir: accountDir || null, incremental }),
  importDouyin: (body) => post('/api/import/douyin', body || {}),
  douyinConversations: () => get('/api/douyin/conversations'),
  douyinFailures: () => get('/api/douyin/failures'),
  douyinList: () => post('/api/douyin/list'),
  douyinCollect: (body) => post('/api/douyin/collect', body || {}),
  douyinRefresh: (body) => post('/api/douyin/refresh', body || {}),

  jobs: () => get('/api/jobs'),
  job: (id) => get(`/api/jobs/${id}`),
  cancelJob: (id) => post(`/api/jobs/${id}/cancel`),

  importVoiceJsonl: (path) => post('/api/voice/import-jsonl', { path }),
  asrStatus: () => get('/api/asr/status'),
  saveAsrConfig: (body) => patch('/api/asr/config', body),
  testAsr: (engine) => post('/api/asr/test', { engine }),
  runAsr: (body) => post('/api/asr/run', body),
  clearVoiceCache: () => post('/api/voice/cache/clear'),

  exportThread: (threadId, format, platform) =>
    post('/api/export', { thread_id: threadId, format, platform: platform || null }),
  exports: () => get('/api/exports'),
}

export const downloadUrl = (name) => `/api/exports/${encodeURIComponent(name)}`
