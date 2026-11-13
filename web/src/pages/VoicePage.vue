<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, refreshStats } from '../lib/store'
import { fmtCount, fmtBytes } from '../lib/format'
import { t } from '../lib/i18n'
import Icon from '../components/Icon.vue'
import JobPanel from '../components/JobPanel.vue'

const status = ref(null)
const loading = ref(true)
const engine = ref('')
const scope = ref('pending')
const limit = ref(500)
const runningJob = ref('')
const testResult = ref('')
const testing = ref(false)

const ENGINE_LABEL = { tencent: '腾讯云一句话识别', local: '本地 faster-whisper' }

const engines = computed(() => status.value?.engines || {})
const counts = computed(() => status.value?.counts || {})
const current = computed(() => engines.value.current || 'off')

const runnable = computed(() => {
  const eng = engine.value
  if (!eng) return false
  if (eng === 'tencent') return !!engines.value.tencent?.available
  if (eng === 'local') return !!engines.value.local?.available
  return false
})

const scopeOptions = computed(() => [
  { key: 'pending', label: '待转写', n: counts.value.pending || 0 },
  { key: 'failed', label: '失败重试', n: counts.value.failed || 0 },
  { key: 'all', label: '全部重跑', n: counts.value.with_audio || 0 },
])

async function load() {
  loading.value = true
  try {
    status.value = await api.asrStatus()
    if (!engine.value) {
      const cur = status.value.engines.current
      engine.value = cur === 'off' ? (status.value.engines.tencent.available ? 'tencent' : 'local') : cur
    }
  } catch (err) {
    toast(`读取识别状态失败：${err.message}`, 'err')
  } finally {
    loading.value = false
  }
}

async function run() {
  if (!runnable.value) {
    toast('这个引擎还不可用，先去设置里配置', 'err')
    return
  }
  try {
    const res = await api.runAsr({
      engine: engine.value,
      scope: scope.value,
      limit: Number(limit.value) || null,
    })
    runningJob.value = res.job.id
    toast('已开始识别', 'ok')
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function onJobDone() {
  runningJob.value = ''
  await load()
  await refreshStats()
}

async function saveDefault() {
  try {
    await api.saveAsrConfig({ engine: engine.value })
    toast(`默认引擎已设为${ENGINE_LABEL[engine.value]}`, 'ok')
    await load()
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function testTencent() {
  testing.value = true
  testResult.value = ''
  try {
    const res = await api.testAsr('tencent')
    testResult.value = res.ok ? '连接成功，密钥可用' : `失败：${res.error}`
  } catch (err) {
    testResult.value = `失败：${err.message}`
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="row">
        <div class="grow">
          <h1>{{ t('语音识别') }}</h1>
          <p>
            把语音消息转成文字。云端用腾讯云一句话识别（按次计费），本地用
            faster-whisper（免费离线）。识别结果会显示在聊天气泡里，并参与搜索。
          </p>
        </div>
        <button class="btn" @click="load"><Icon name="refresh" :size="14" /> {{ t('刷新') }}</button>
      </div>
    </div>

    <div v-if="loading" class="row"><div class="spinner" /> <span class="hint">读取中…</span></div>

    <template v-else>
      <div class="stat-grid" style="grid-template-columns: repeat(5, minmax(0, 1fr))">
        <div class="stat">
          <div class="n">{{ fmtCount(counts.total) }}</div>
          <div class="l">{{ t('语音消息') }}</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(counts.with_audio) }}</div>
          <div class="l">{{ t('有音频') }}</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(counts.pending) }}</div>
          <div class="l">{{ t('待转写') }}</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(counts.success) }}</div>
          <div class="l">{{ t('已转写') }}</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(counts.failed) }}</div>
          <div class="l">{{ t('失败/空结果') }}</div>
        </div>
      </div>

      <div class="grid two mt20">
        <div class="card">
          <div class="row" style="margin-bottom: 14px">
            <Icon name="mic" :size="18" />
            <span class="grow" style="font-weight: 600">{{ t('开始识别') }}</span>
            <span class="chip" :class="{ active: current !== 'off' }">
              默认：{{ current === 'off' ? '未开启' : ENGINE_LABEL[current] }}
            </span>
          </div>

          <div class="field">
            <label>{{ t('引擎') }}</label>
            <select v-model="engine" class="select">
              <option value="tencent" :disabled="!engines.tencent?.available">
                腾讯云一句话识别{{ engines.tencent?.available ? '' : '（未配置密钥）' }}
              </option>
              <option value="local" :disabled="!engines.local?.available">
                本地 faster-whisper（{{ engines.local?.model }}）{{ engines.local?.available ? '' : '（未安装）' }}
              </option>
            </select>
          </div>

          <div class="field">
            <label>{{ t('范围') }}</label>
            <div class="row wrap" style="gap: 8px">
              <button
                v-for="opt in scopeOptions"
                :key="opt.key"
                class="chip"
                :class="{ active: scope === opt.key }"
                @click="scope = opt.key"
              >
                {{ t(opt.label) }} {{ fmtCount(opt.n) }}
              </button>
            </div>
          </div>

          <div class="field">
            <label>{{ t('本次最多处理（条）') }}</label>
            <input v-model.number="limit" class="input" type="number" min="1" max="5000" />
          </div>

          <div class="row wrap" style="gap: 8px">
            <button class="btn primary" :disabled="!runnable" @click="run">
              <Icon name="play" :size="14" /> {{ t('开始识别') }}
            </button>
            <button class="btn" @click="saveDefault">
              {{ t('设为默认引擎') }}
            </button>
            <button v-if="engine === 'tencent'" class="btn ghost" :disabled="testing" @click="testTencent">
              <span v-if="testing" class="spinner" />
              <Icon v-else name="link" :size="14" /> {{ t('测试密钥') }}
            </button>
          </div>
          <div v-if="testResult" class="hint mt12">{{ testResult }}</div>
          <div class="hint mt12">
            云端约 1 元/千次；本地首次识别会下载模型（base≈150MB、small≈500MB、medium≈1.5GB）。
          </div>
        </div>

        <div class="card">
          <div class="row" style="margin-bottom: 12px">
            <span class="grow" style="font-weight: 600">{{ t('引擎状态') }}</span>
            <button class="btn sm ghost" @click="store.view = 'settings'">{{ t('设置') }}</button>
          </div>

          <div class="kv"><span>{{ t('默认引擎') }}</span>
            <span>{{ current === 'off' ? '未开启' : ENGINE_LABEL[current] }}</span>
          </div>
          <div class="kv"><span>{{ t('导入后自动识别') }}</span>
            <span>{{ engines.auto ? '开' : '关' }}</span>
          </div>
          <div class="kv"><span>{{ t('云端并发') }}</span><span>{{ engines.concurrency }}</span></div>
          <div class="kv"><span>{{ t('腾讯云密钥') }}</span>
            <span>{{ engines.tencent?.available ? '已配置' : '未配置' }}</span>
          </div>
          <div class="kv"><span>{{ t('本地模型') }}</span>
            <span>{{ engines.local?.available ? engines.local?.model : '不可用' }}</span>
          </div>
          <div class="kv" v-if="status?.secrets?.tencent?.secret_id_masked">
            <span>SecretId</span><span class="mono">{{ status.secrets.tencent.secret_id_masked }}</span>
          </div>

          <div class="divider" />
          <div class="hint">
            识别结果保存在本机；同一条语音重复识别会覆盖旧结果。
            失败的消息可以切到「失败重试」再跑一遍。
          </div>
        </div>
      </div>

      <div v-if="runningJob" class="mt20">
        <JobPanel :job-id="runningJob" title="语音识别" @done="onJobDone" />
      </div>
    </template>
  </div>
</template>
