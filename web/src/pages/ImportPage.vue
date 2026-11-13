<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../lib/api'
import { store, toast, refreshStats } from '../lib/store'
import { fmtBytes, fmtCount, fmtDateTime } from '../lib/format'
import { t } from '../lib/i18n'
import Icon from '../components/Icon.vue'
import JobPanel from '../components/JobPanel.vue'

const sources = ref(null)
const loading = ref(true)
const activeJob = ref(null)
const activeTitle = ref('')
const recent = ref([])
const customDb = ref('')
const voiceJsonl = ref('')

// ── 抖音采集范围 ──
const wxIncremental = ref(true)
const dyConvs = ref([])
const dyConvReady = ref(false)
const dyLoading = ref(false)
const dyMode = ref(store.settings.douyin_scope_mode || 'all')
const dySelected = ref([...(store.settings.douyin_scope_names || [])])
const dyQuery = ref('')
let scopeTimer = null

function persistScope() {
  return api.saveSettings({
    douyin_scope_mode: dyMode.value,
    douyin_scope_names: dySelected.value,
  }).catch(() => {})
}

watch([dyMode, dySelected], () => {
  clearTimeout(scopeTimer)
  scopeTimer = setTimeout(persistScope, 400)
}, { deep: true })
const dyIncremental = ref(true)
const dyDownloadImages = ref(false)
const dyFailures = ref([])
const lastJobKind = ref('')

const filteredConvs = computed(() => {
  const q = dyQuery.value.trim().toLowerCase()
  if (!q) return dyConvs.value
  return dyConvs.value.filter((c) => (c.name || '').toLowerCase().includes(q))
})

async function loadDouyinFailures() {
  try {
    const res = await api.douyinFailures()
    dyFailures.value = res.items || []
  } catch {
    dyFailures.value = []
  }
}

async function loadDouyinConversations() {
  dyLoading.value = true
  try {
    const res = await api.douyinConversations()
    dyConvs.value = res.items || []
    dyConvReady.value = !!res.available
  } catch (err) {
    toast(`读取会话列表失败：${err.message}`, 'err')
  } finally {
    dyLoading.value = false
  }
}

function toggleConv(name) {
  const idx = dySelected.value.indexOf(name)
  if (idx >= 0) dySelected.value.splice(idx, 1)
  else dySelected.value.push(name)
}

function selectAllConvs() {
  dySelected.value = filteredConvs.value.map((c) => c.name)
}

function clearConvs() {
  dySelected.value = []
}

// 采集用哪个浏览器：系统 Edge 优先，其次 Playwright 的 Chromium
const browserText = computed(() => {
  const e = sources.value?.engines?.douyin
  if (!e) return '检测中…'
  if (!e.playwright) return '未安装'
  if (e.channel === 'msedge') return '可用（系统 Edge）'
  if (e.channel === 'chromium') return '可用（内置 Chromium）'
  return '缺少可用浏览器'
})
const browserReady = computed(() => !!sources.value?.engines?.douyin?.ready)

async function refresh() {
  loading.value = true
  try {
    sources.value = await api.sources()
    recent.value = (await api.jobs()).items || []
  } catch (err) {
    toast(`读取数据源失败：${err.message}`, 'err')
  } finally {
    loading.value = false
  }
}

const STATUS_LABEL = {
  queued: '排队中',
  running: '进行中',
  ok: '完成',
  failed: '失败',
  cancelled: '已取消',
}

async function run(kind, title, arg) {
  const fn = {
    wechatRefresh: () => api.wechatRefresh({ incremental: wxIncremental.value }),
    wechatDecrypt: () => api.wechatDecrypt(),
    wechatImport: () => api.importWechat(null, wxIncremental.value),
    wechatImportOne: () => api.importWechat(arg, wxIncremental.value),
    douyinRefresh: () => api.douyinRefresh({
      incremental: dyIncremental.value,
      mode: dyMode.value,
      conversations: dySelected.value,
      download_images: dyDownloadImages.value,
    }),
    douyinCollectOnly: () => api.douyinCollect({
      incremental: dyIncremental.value,
      mode: dyMode.value,
      conversations: dySelected.value,
      download_images: dyDownloadImages.value,
    }),
    douyinList: () => api.douyinList(),
    douyinRetry: () => api.douyinCollect({
      incremental: true,
      mode: 'include',
      conversations: dyFailures.value,
      download_images: dyDownloadImages.value,
    }),
    douyinImport: () => api.importDouyin({ copy_media: true }),
    douyinCustom: () => api.importDouyin({ chat_db: customDb.value.trim(), copy_media: true }),
  }[kind]
  try {
    const res = await fn()
    lastJobKind.value = kind
    activeJob.value = res.job.id
    activeTitle.value = title
    recent.value = (await api.jobs()).items || []
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function importVoice() {
  const path = voiceJsonl.value.trim()
  if (!path) return
  try {
    const res = await api.importVoiceJsonl(path)
    const extra = res.empty ? `（${res.empty} 条空文本已跳过）` : ''
    toast(`语音转写已导入：匹配 ${res.matched} / 共 ${res.total} 条${extra}`, 'ok')
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function onJobDone() {
  await refresh()
  await refreshStats()
  await loadDouyinFailures()
  await loadDouyinConversations()
  // 拉完会话列表后自动切到「只采集勾选」，用户可以直接勾选范围
  if (lastJobKind.value === 'douyinList' && dyConvs.value.length && dyMode.value === 'all') {
    dyMode.value = 'include'
  }
  lastJobKind.value = ''
  activeJob.value = null
}

onMounted(async () => {
  loadDouyinFailures()
  const scope = new URLSearchParams(location.search).get('scope')
  if (scope === 'include' || scope === 'exclude') dyMode.value = scope
  await refresh()
  await loadDouyinConversations()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('导入数据') }}</h1>
      <p>
        微信和抖音的原始数据分别由内置引擎读取，统一写进本机的
        <code class="mono">{{ sources?.data_dir || 'data/' }}</code>。导入过程不会上传任何内容。
      </p>
    </div>

    <div v-if="loading" class="row"><div class="spinner" /> <span class="hint">读取中…</span></div>

    <template v-else-if="sources">
      <JobPanel
        v-if="activeJob"
        :job-id="activeJob"
        :title="activeTitle"
        @done="onJobDone"
      />

      <div class="grid two mt20">
        <!-- 微信 -->
        <div class="card">
          <div class="row" style="margin-bottom: 14px">
            <div class="avatar" style="background: var(--wechat-soft); color: var(--wechat); width: 38px; height: 38px">
              <Icon name="chat" :size="19" />
            </div>
            <div class="grow">
              <div style="font-weight: 600">{{ t('微信聊天记录') }}</div>
              <div class="faint small">解密 → 读取 message_*.db → 统一库</div>
            </div>
          </div>

          <div class="kv"><span>内置解密引擎</span>
            <span :class="sources.engines.wechat.bundled ? '' : 'faint'">
              {{ sources.engines.wechat.bundled ? '可用（SIWX）' : '缺失' }}
            </span>
          </div>
          <div class="kv"><span>已解密账号</span><span>{{ sources.wechat_accounts.length }} 个</span></div>

          <div v-if="sources.wechat_accounts.length" class="rows mt12">
            <div v-for="acc in sources.wechat_accounts" :key="acc.path" class="row-card">
              <Icon name="database" :size="16" />
              <div class="row-main">
                <div class="row-title">{{ acc.name }}</div>
                <div class="row-sub">
                  {{ acc.shards }} 个分片 · {{ fmtBytes(acc.size) }} · {{ fmtDateTime(acc.mtime) }}
                </div>
              </div>
              <button class="btn sm" @click="run('wechatImportOne', `导入 ${acc.name}`, acc.path)">
                导入
              </button>
            </div>
          </div>

          <div class="field mt12">
            <label>回填旧版语音转写（voice_api.jsonl，可选）</label>
            <div class="row" style="gap: 8px">
              <input v-model="voiceJsonl" class="input" placeholder="选择旧项目的 voice_api.jsonl" />
              <button class="btn sm" :disabled="!voiceJsonl" @click="importVoice">导入</button>
            </div>
          </div>

          <div class="hint mt12">
            解密需要微信正在运行并已登录；数据只从本机读取。
          </div>

          <div class="row wrap mt12" style="gap: 8px">
            <button class="btn primary" @click="run('wechatRefresh', '微信：解密并导入')">
              <Icon name="refresh" :size="14" /> {{ t('解密并导入') }}
            </button>
            <button
              class="btn"
              :disabled="!sources.wechat_accounts.length"
              @click="run('wechatImport', '导入已解密的微信数据')"
            >
              <Icon name="import" :size="14" /> {{ t('只导入已解密数据') }}
            </button>
          </div>
        </div>

        <!-- 抖音 -->
        <div class="card">
          <div class="row" style="margin-bottom: 14px">
            <div class="avatar" style="background: var(--douyin-soft); color: var(--douyin); width: 38px; height: 38px">
              <Icon name="video" :size="19" />
            </div>
            <div class="grow">
              <div style="font-weight: 600">{{ t('抖音聊天记录') }}</div>
              <div class="faint small">内置采集引擎 → chat.db → 统一库</div>
            </div>
          </div>

          <div class="kv"><span>引擎数据库</span>
            <span>{{ sources.douyin.exists ? '已存在' : '还没有' }}</span>
          </div>
          <div class="kv"><span>会话 / 消息</span>
            <span>{{ fmtCount(sources.douyin.conversations) }} / {{ fmtCount(sources.douyin.messages) }}</span>
          </div>
          <div class="kv"><span>语音转写</span><span>{{ fmtCount(sources.douyin.voice) }} 条</span></div>
          <div class="kv"><span>采集浏览器</span>
            <span :class="browserReady ? '' : 'faint'">{{ browserText }}</span>
          </div>

          <div class="field mt12">
            <label>{{ t('采集范围') }}</label>
            <div class="row wrap" style="gap: 6px">
              <button class="chip" :class="{ active: dyMode === 'all' }" @click="dyMode = 'all'">{{ t('全部会话') }}</button>
              <button class="chip" :class="{ active: dyMode === 'include' }" @click="dyMode = 'include'">{{ t('只采集勾选') }}</button>
              <button class="chip" :class="{ active: dyMode === 'exclude' }" @click="dyMode = 'exclude'">{{ t('排除勾选') }}</button>
              <button class="btn sm ghost" :disabled="dyLoading" @click="loadDouyinConversations">
                <span v-if="dyLoading" class="spinner" />
                <Icon v-else name="refresh" :size="12" />
                {{ dyConvReady ? t('刷新本地列表') : t('加载会话') }}
              </button>
              <button
                class="btn sm"
                :disabled="!browserReady"
                :title="browserReady ? '扫码登录，只获取会话列表，不采集消息' : '缺少可用浏览器'"
                @click="run('douyinList', '抖音：获取会话列表')"
              >
                <Icon name="import" :size="12" /> {{ t('从抖音拉列表') }}
              </button>
            </div>
            <div class="hint mt12" v-if="dyMode === 'exclude'">
              排除模式基于本地已有会话列表；列表里的会话会全部重抓。
            </div>
          </div>

          <div v-if="dyMode !== 'all'" class="conv-picker">
            <div class="row" style="gap: 8px; margin-bottom: 8px">
              <input v-model="dyQuery" class="input" placeholder="搜索会话…" />
              <button class="btn sm ghost" @click="selectAllConvs">全选</button>
              <button class="btn sm ghost" @click="clearConvs">清空</button>
            </div>
            <div class="conv-list">
              <button
                v-for="c in filteredConvs"
                :key="c.name"
                type="button"
                class="conv-row"
                :aria-checked="dySelected.includes(c.name)"
                @click="toggleConv(c.name)"
              >
                <span class="checkbox" :class="{ on: dySelected.includes(c.name) }">
                  <Icon name="check" :size="11" />
                </span>
                <span class="grow conv-name">{{ c.name }}</span>
                <span v-if="c.kind === 'group'" class="group-tag">群</span>
                <span class="faint small nowrap">{{ fmtCount(c.message_count) }}</span>
              </button>
              <div v-if="!filteredConvs.length" class="hint" style="padding: 10px">
                还没有会话列表。点「从抖音拉列表」扫码登录，只读取会话名单
                （不会采集消息），然后就能勾选范围了。
              </div>
            </div>
            <div class="hint" style="margin-top: 6px">
              已选 {{ dySelected.length }} / {{ dyConvs.length }}
              · 定时增量导入也按这个范围（全部 / 勾选 / 排除）
            </div>
          </div>

          <div class="hint" v-if="dyMode === 'include' && !dySelected.length" style="margin-top: 6px">
            先勾选要采集的会话，再点「采集并导入」。
          </div>

          <div class="row mt12">
            <div class="grow">
              <div style="font-weight: 550">{{ t('增量采集') }}</div>
              <div class="hint">只取新消息；关掉则把整个会话重新抓一遍</div>
            </div>
            <button class="switch" :class="{ on: dyIncremental }" @click="dyIncremental = !dyIncremental" />
          </div>

          <div class="row mt12">
            <div class="grow">
              <div style="font-weight: 550">{{ t('下载媒体') }}</div>
              <div class="hint">采集时一并保存媒体，会明显变慢</div>
            </div>
            <button class="switch" :class="{ on: dyDownloadImages }" @click="dyDownloadImages = !dyDownloadImages" />
          </div>

          <div v-if="dyFailures.length" class="row mt12" style="gap: 8px">
            <div class="grow">
              <div style="font-weight: 550">上次失败的会话：{{ dyFailures.length }} 个</div>
              <div class="hint">{{ dyFailures.slice(0, 3).join('、') }}{{ dyFailures.length > 3 ? '…' : '' }}</div>
            </div>
            <button class="btn sm" @click="run('douyinRetry', '抖音：重试失败会话')">
              <Icon name="refresh" :size="13" /> 重试
            </button>
          </div>

          <div class="field mt12">
            <label>导入其它位置的 chat.db（可选）</label>
            <input v-model="customDb" class="input" placeholder="D:\douyin\data\chat.db" />
          </div>

          <div class="row wrap mt12" style="gap: 8px">
            <button
              class="btn primary"
              :disabled="!browserReady || (dyMode === 'include' && !dySelected.length)"
              :title="browserReady ? '会打开浏览器窗口，请在窗口里扫码登录' : '缺少可用浏览器，请安装 Edge'"
              @click="run('douyinRefresh', '抖音：采集并导入')"
            >
              <Icon name="refresh" :size="14" /> 采集并导入
            </button>
            <button class="btn" :disabled="!sources.douyin.exists" @click="run('douyinImport', '导入本地抖音数据库')">
              <Icon name="import" :size="14" /> {{ t('导入本地数据库') }}
            </button>
            <button class="btn ghost" :disabled="!customDb" @click="run('douyinCustom', '导入指定抖音数据库')">
              {{ t('导入上面的路径') }}
            </button>
          </div>
        </div>
      </div>

      <div class="section mt20">
        <h2>最近的任务</h2>
        <div v-if="!recent.length" class="hint">还没有运行过任务。</div>
        <div v-else class="rows">
          <div v-for="j in recent.slice(0, 8)" :key="j.id" class="row-card">
            <Icon :name="j.kind.startsWith('wechat') ? 'chat' : 'video'" :size="15" />
            <div class="row-main">
              <div class="row-title">{{ j.title }}</div>
              <div class="row-sub">{{ fmtDateTime(j.started_at) }}</div>
            </div>
            <span class="chip" :class="{ active: j.status === 'running' || j.status === 'queued' }">
              {{ STATUS_LABEL[j.status] || j.status }}
            </span>
            <button class="btn sm ghost" @click="(activeJob = j.id), (activeTitle = j.title)">日志</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.conv-picker { margin: 8px 0 4px; }
.conv-list {
  max-height: 220px;
  overflow-y: auto;
  border: 1px solid var(--border-soft);
  border-radius: 9px;
  padding: 4px;
}
.conv-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
  margin: 0;
}
.conv-row:hover { background: var(--panel-2); }
.conv-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
