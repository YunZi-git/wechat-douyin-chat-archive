<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, applyDensity, refreshStats, currentTheme } from '../lib/store'
import { fmtCount, fmtDateTime, fmtBytes } from '../lib/format'
import Icon from '../components/Icon.vue'

const saving = ref(false)
const clearing = ref(false)

// ── 语音识别 ──
const asr = ref(null)
const asrForm = ref({
  engine: 'off',
  auto_after_import: false,
  concurrency: 2,
  whisper_model: 'small',
  whisper_model_dir: '',
  whisper_endpoint: '',
  tencent_secret_id: '',
  tencent_secret_key: '',
  tencent_region: 'ap-shanghai',
})
const asrSaving = ref(false)
const asrTesting = ref(false)
const asrMessage = ref('')

async function loadAsr() {
  try {
    asr.value = await api.asrStatus()
    const engines = asr.value.engines
    asrForm.value.engine = engines.current || 'off'
    asrForm.value.auto_after_import = !!engines.auto
    asrForm.value.concurrency = engines.concurrency || 2
    asrForm.value.whisper_model = engines.local?.model || 'small'
    asrForm.value.whisper_endpoint = asr.value.engines?.local?.endpoint || ''
    asrForm.value.tencent_region = asr.value.secrets?.tencent?.region || 'ap-shanghai'
  } catch (err) {
    asrMessage.value = `读取识别配置失败：${err.message}`
  }
}

async function saveAsr() {
  asrSaving.value = true
  asrMessage.value = ''
  try {
    const body = {
      engine: asrForm.value.engine,
      auto_after_import: asrForm.value.auto_after_import,
      concurrency: Number(asrForm.value.concurrency) || 2,
      whisper_model: asrForm.value.whisper_model,
      whisper_model_dir: asrForm.value.whisper_model_dir,
      whisper_endpoint: asrForm.value.whisper_endpoint,
      tencent_region: asrForm.value.tencent_region,
    }
    // 留空表示不修改已保存的密钥
    if (asrForm.value.tencent_secret_id) body.tencent_secret_id = asrForm.value.tencent_secret_id
    if (asrForm.value.tencent_secret_key) body.tencent_secret_key = asrForm.value.tencent_secret_key
    const res = await api.saveAsrConfig(body)
    asr.value = { engines: res.engines, counts: res.counts, secrets: res.secrets }
    asrForm.value.tencent_secret_id = ''
    asrForm.value.tencent_secret_key = ''
    asrMessage.value = '已保存'
    toast('语音识别配置已保存', 'ok')
  } catch (err) {
    asrMessage.value = `保存失败：${err.message}`
  } finally {
    asrSaving.value = false
  }
}

async function testAsr() {
  asrTesting.value = true
  asrMessage.value = ''
  try {
    const res = await api.testAsr('tencent')
    asrMessage.value = res.ok ? '腾讯云连接成功' : `腾讯云测试失败：${res.error}`
  } catch (err) {
    asrMessage.value = `测试失败：${err.message}`
  } finally {
    asrTesting.value = false
  }
}

async function clearAsrKeys() {
  try {
    const res = await api.saveAsrConfig({ tencent_secret_id: '', tencent_secret_key: '' })
    asr.value = { engines: res.engines, counts: res.counts, secrets: res.secrets }
    asrMessage.value = '密钥已清除'
  } catch (err) {
    asrMessage.value = `清除失败：${err.message}`
  }
}
const health = ref(null)
const lastImport = computed(() => store.stats?.last_import)

async function save(patch) {
  Object.assign(store.settings, patch)
  saving.value = true
  try {
    await api.saveSettings(patch)
  } catch (err) {
    toast(`保存失败：${err.message}`, 'err')
  } finally {
    saving.value = false
  }
}

async function setDensity(value) {
  applyDensity(value)
  await save({ density: value })
}

async function clearCache() {
  clearing.value = true
  try {
    const res = await api.clearVoiceCache()
    toast(`已清理 ${fmtBytes(res.freed_bytes)} 的语音缓存`, 'ok')
    await refreshStats()
  } catch (err) {
    toast(`清理失败：${err.message}`, 'err')
  } finally {
    clearing.value = false
  }
}

async function setPageSize(event) {
  const value = Number(event.target.value)
  await save({ page_size: value })
  toast(`每页 ${value} 条`)
}

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch {
    /* 忽略 */
  }
  await refreshStats()
  await loadAsr()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>设置</h1>
      <p>所有设置只保存在本机 <code class="mono">data/settings.json</code>，不会上传。</p>
    </div>

    <div class="section">
      <h2>外观</h2>
      <div class="card">
        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">当前外观：{{ currentTheme().name }}</div>
            <div class="hint">
              内置 4 套 + StyleKit 151 套；配色、圆角、边框和阴影都会跟着变
            </div>
          </div>
          <button class="btn" @click="store.view = 'appearance'">
            <Icon name="sparkles" :size="14" /> 浏览外观库
          </button>
        </div>

        <div class="divider" />

        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">紧凑模式</div>
            <div class="hint">消息行距更小，一屏能看更多</div>
          </div>
          <button class="switch" :class="{ on: store.settings.density === 'compact' }" @click="setDensity(store.settings.density === 'compact' ? 'comfortable' : 'compact')" />
        </div>
      </div>
    </div>

    <div class="section">
      <h2>阅读</h2>
      <div class="card">
        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">每次加载消息条数</div>
            <div class="hint">打开会话和向上翻页时的批量大小</div>
          </div>
          <select class="select" style="max-width: 130px" :value="store.settings.page_size" @change="setPageSize">
            <option :value="40">40 条</option>
            <option :value="60">60 条</option>
            <option :value="100">100 条</option>
            <option :value="200">200 条</option>
          </select>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>语音识别</h2>
      <div class="card">
        <div class="field">
          <label>默认引擎</label>
          <div class="row wrap" style="gap: 8px">
            <button class="chip" :class="{ active: asrForm.engine === 'off' }" @click="asrForm.engine = 'off'">关闭</button>
            <button class="chip" :class="{ active: asrForm.engine === 'tencent' }" @click="asrForm.engine = 'tencent'">腾讯云</button>
            <button class="chip" :class="{ active: asrForm.engine === 'local' }" @click="asrForm.engine = 'local'">本地 whisper</button>
          </div>
          <div class="hint mt12">
            「关闭」只是不默认使用；导入后自动识别也依赖它。
          </div>
        </div>

        <div class="divider" />

        <div class="row" style="margin-bottom: 10px">
          <span class="grow" style="font-weight: 550">腾讯云一句话识别</span>
          <span class="chip" :class="{ active: asr?.engines?.tencent?.available }">
            {{ asr?.engines?.tencent?.available ? '已配置' : '未配置' }}
          </span>
        </div>

        <div class="grid two" style="gap: 12px">
          <div class="field" style="margin-bottom: 0">
            <label>SecretId</label>
            <input
              v-model="asrForm.tencent_secret_id"
              class="input"
              :placeholder="asr?.secrets?.tencent?.secret_id_masked || 'AKID...'"
            />
          </div>
          <div class="field" style="margin-bottom: 0">
            <label>SecretKey</label>
            <input
              v-model="asrForm.tencent_secret_key"
              class="input"
              type="password"
              :placeholder="asr?.secrets?.tencent?.secret_key_masked || '留空表示不修改'"
            />
          </div>
        </div>
        <div class="field mt12" style="max-width: 220px">
          <label>地域</label>
          <input v-model="asrForm.tencent_region" class="input" placeholder="ap-shanghai" />
        </div>

        <div class="row wrap" style="gap: 8px">
          <button class="btn sm" :disabled="asrTesting" @click="testAsr">
            <span v-if="asrTesting" class="spinner" />
            <Icon v-else name="link" :size="13" /> 测试连接
          </button>
          <button class="btn sm ghost danger" @click="clearAsrKeys">清除密钥</button>
          <span class="hint">密钥存在 data/secrets.json，不进仓库；云端约 1 元/千次。</span>
        </div>

        <div class="divider" />

        <div class="row" style="margin-bottom: 10px">
          <span class="grow" style="font-weight: 550">本地 faster-whisper</span>
          <span class="chip" :class="{ active: asr?.engines?.local?.available }">
            {{ asr?.engines?.local?.available ? '可用' : '未安装' }}
          </span>
        </div>

        <div class="grid two" style="gap: 12px">
          <div class="field" style="margin-bottom: 0">
            <label>模型大小</label>
            <select v-model="asrForm.whisper_model" class="select">
              <option value="base">base（最快，准确率一般）</option>
              <option value="small">small（推荐）</option>
              <option value="medium">medium（更准，更慢）</option>
            </select>
          </div>
          <div class="field" style="margin-bottom: 0">
            <label>模型目录（可选）</label>
            <input v-model="asrForm.whisper_model_dir" class="input" placeholder="留空用默认缓存目录" />
          </div>
          <div class="field" style="margin-bottom: 0">
            <label>模型下载镜像（可选）</label>
            <input
              v-model="asrForm.whisper_endpoint"
              class="input"
              placeholder="https://hf-mirror.com"
            />
          </div>
        </div>
        <div class="hint mt12">
          首次识别会自动下载模型（base≈150MB / small≈500MB / medium≈1.5GB），之后完全离线。
          国内网络建议把「下载镜像」填成 https://hf-mirror.com（改完重启应用生效）。
        </div>

        <div class="divider" />

        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">导入后自动识别新语音</div>
            <div class="hint">每次导入结束自动跑一批待转写语音（最多 200 条）</div>
          </div>
          <button
            class="switch"
            :class="{ on: asrForm.auto_after_import }"
            @click="asrForm.auto_after_import = !asrForm.auto_after_import"
          />
        </div>

        <div class="row mt12">
          <div class="grow">
            <div style="font-weight: 550">云端并发</div>
            <div class="hint">本地识别固定串行，避免抢 CPU</div>
          </div>
          <select v-model.number="asrForm.concurrency" class="select" style="max-width: 110px">
            <option :value="1">1</option>
            <option :value="2">2</option>
            <option :value="4">4</option>
          </select>
        </div>

        <div class="row mt20" style="gap: 10px">
          <button class="btn primary" :disabled="asrSaving" @click="saveAsr">
            <span v-if="asrSaving" class="spinner" />
            保存识别配置
          </button>
          <span v-if="asrMessage" class="hint">{{ asrMessage }}</span>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>数据</h2>
      <div class="stat-grid" style="margin-bottom: 14px">
        <div class="stat">
          <div class="n">{{ fmtCount(store.stats?.messages) }}</div>
          <div class="l">消息总数</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(store.stats?.contacts) }}</div>
          <div class="l">联系人</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(store.stats?.conversations) }}</div>
          <div class="l">会话（含群）</div>
        </div>
        <div class="stat">
          <div class="n">{{ fmtCount(store.stats?.merged_contacts) }}</div>
          <div class="l">已合并好友</div>
        </div>
      </div>

      <div class="card">
        <div class="kv"><span>数据目录</span><span class="mono" style="font-size: 11.5px">{{ health?.data_dir || 'data/' }}</span></div>
        <div class="kv"><span>微信消息</span><span>{{ fmtCount(store.stats?.messages_by_platform?.wechat) }}</span></div>
        <div class="kv"><span>抖音消息</span><span>{{ fmtCount(store.stats?.messages_by_platform?.douyin) }}</span></div>
        <div class="kv">
          <span>微信语音</span>
          <span>
            {{ fmtCount(store.stats?.voice_messages) }} 条
            （{{ fmtCount(store.stats?.voice_with_audio) }} 条有音频）
          </span>
        </div>
        <div class="kv"><span>语音转写</span><span>{{ fmtCount(store.stats?.transcripts) }} 条</span></div>
        <div class="kv">
          <span>转码缓存</span>
          <span class="row" style="gap: 8px">
            {{ fmtBytes(store.stats?.voice_cache?.bytes) }}
            <button class="btn sm ghost" :disabled="clearing || !store.stats?.voice_cache?.bytes" @click="clearCache">
              清理
            </button>
          </span>
        </div>
        <div class="kv" v-if="lastImport">
          <span>最近一次导入</span>
          <span>{{ lastImport.platform === 'wechat' ? '微信' : '抖音' }} · {{ fmtDateTime(lastImport.finished_at) }} · +{{ fmtCount(lastImport.messages_added) }} 条</span>
        </div>
        <div class="hint mt12">
          备份请直接复制整个 <code class="mono">data/</code> 目录；换机器时把它带到新目录即可。
        </div>
      </div>
    </div>

    <div class="section">
      <h2>关于</h2>
      <div class="card">
        <div class="row" style="margin-bottom: 14px">
          <div class="rail-logo" style="margin: 0; width: 42px; height: 42px">聊</div>
          <div class="grow">
            <div style="font-weight: 600">聊天信息工作台</div>
            <div class="hint">把微信与抖音的聊天记录合并成一条可浏览、可导出的时间线</div>
          </div>
          <span class="chip">v{{ health?.version || '0.1.0' }}</span>
        </div>

        <div class="kv"><span>许可证</span><span>AGPL-3.0</span></div>
        <div class="kv"><span>微信解密组件</span><span>SIWX（AGPL-3.0）</span></div>
        <div class="kv"><span>抖音采集组件</span><span>douyin-chat-export（MIT）</span></div>

        <div class="divider" />

        <div class="hint">
          本项目不收集、不上传任何聊天内容。仓库里不含任何真实聊天数据、密钥或账号信息，
          演示数据全部是程序生成的合成数据。
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
@media (max-width: 720px) {
  }
</style>
