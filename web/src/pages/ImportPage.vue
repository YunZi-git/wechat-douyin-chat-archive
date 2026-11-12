<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, refreshStats } from '../lib/store'
import { fmtBytes, fmtCount, fmtDateTime } from '../lib/format'
import Icon from '../components/Icon.vue'
import JobPanel from '../components/JobPanel.vue'

const sources = ref(null)
const loading = ref(true)
const activeJob = ref(null)
const activeTitle = ref('')
const recent = ref([])
const customDb = ref('')

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
    wechatRefresh: () => api.wechatRefresh(),
    wechatDecrypt: () => api.wechatDecrypt(),
    wechatImport: () => api.importWechat(),
    wechatImportOne: () => api.importWechat(arg),
    douyinRefresh: () => api.douyinRefresh({ incremental: true }),
    douyinImport: () => api.importDouyin({ copy_media: true }),
    douyinCustom: () => api.importDouyin({ chat_db: customDb.value.trim(), copy_media: true }),
  }[kind]
  try {
    const res = await fn()
    activeJob.value = res.job.id
    activeTitle.value = title
    recent.value = (await api.jobs()).items || []
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function onJobDone() {
  await refresh()
  await refreshStats()
  activeJob.value = null
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>导入数据</h1>
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
              <div style="font-weight: 600">微信聊天记录</div>
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

          <div class="hint mt12">
            解密需要微信正在运行并已登录；数据只从本机读取。
          </div>

          <div class="row wrap mt12" style="gap: 8px">
            <button class="btn primary" @click="run('wechatRefresh', '微信：解密并导入')">
              <Icon name="refresh" :size="14" /> 解密并导入
            </button>
            <button
              class="btn"
              :disabled="!sources.wechat_accounts.length"
              @click="run('wechatImport', '导入已解密的微信数据')"
            >
              <Icon name="import" :size="14" /> 只导入已解密数据
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
              <div style="font-weight: 600">抖音聊天记录</div>
              <div class="faint small">内置采集引擎 → chat.db → 统一库</div>
            </div>
          </div>

          <div class="kv"><span>本地数据库</span>
            <span>{{ sources.douyin.exists ? '已存在' : '还没有' }}</span>
          </div>
          <div class="kv"><span>会话 / 消息</span>
            <span>{{ fmtCount(sources.douyin.conversations) }} / {{ fmtCount(sources.douyin.messages) }}</span>
          </div>
          <div class="kv"><span>语音转写</span><span>{{ fmtCount(sources.douyin.voice) }} 条</span></div>
          <div class="kv"><span>采集引擎（Playwright）</span>
            <span :class="sources.engines.douyin.playwright ? '' : 'faint'">
              {{ sources.engines.douyin.playwright ? '可用' : '未安装' }}
            </span>
          </div>

          <div class="field mt12">
            <label>导入其它位置的 chat.db（可选）</label>
            <input v-model="customDb" class="input" placeholder="D:\douyin\data\chat.db" />
          </div>

          <div class="row wrap mt12" style="gap: 8px">
            <button
              class="btn primary"
              :disabled="!sources.engines.douyin.playwright"
              :title="sources.engines.douyin.playwright ? '' : '需要安装 Playwright 才能启动浏览器采集'"
              @click="run('douyinRefresh', '抖音：采集并导入')"
            >
              <Icon name="refresh" :size="14" /> 采集并导入
            </button>
            <button class="btn" :disabled="!sources.douyin.exists" @click="run('douyinImport', '导入本地抖音数据库')">
              <Icon name="import" :size="14" /> 导入本地数据库
            </button>
            <button class="btn ghost" :disabled="!customDb" @click="run('douyinCustom', '导入指定抖音数据库')">
              导入上面的路径
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
