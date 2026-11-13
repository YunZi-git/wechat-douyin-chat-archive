<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, applyDensity, refreshStats, currentTheme } from '../lib/store'
import { fmtCount, fmtDateTime, fmtBytes } from '../lib/format'
import { t, setLanguage, i18n } from '../lib/i18n'
import Icon from '../components/Icon.vue'
import JobPanel from '../components/JobPanel.vue'

const saving = ref(false)
const clearing = ref(false)

// ── 自动增量更新 ──
const identity = ref(null)
const selfPick = ref({ wechat: '', douyin: '' })

async function loadIdentity() {
  try {
    identity.value = await api.selfIdentity()
    selfPick.value = {
      wechat: identity.value.platforms.wechat?.uid || '',
      douyin: identity.value.platforms.douyin?.uid || '',
    }
  } catch {
    identity.value = null
  }
}

async function saveSelf(platform) {
  const uid = selfPick.value[platform]
  if (!uid) return
  try {
    await api.setSelfIdentity({ platform, uid })
    toast('已更新「我」的身份，正在刷新…', 'ok')
    setTimeout(() => window.location.reload(), 600)
  } catch (err) {
    toast(err.message, 'err')
  }
}

function selfSource(p) {
  if (!p.uid) return '未设置'
  if (p.source === 'manual') return '已手动确认'
  if (p.source === 'account') return '登录账号自动识别'
  if (p.source === 'guessed' || p.source === 'saved') return '自动推断'
  return '已识别'
}

const autosync = ref(null)
const syncJobId = ref('')
const syncBusy = ref(false)

async function loadAutosync() {
  try {
    autosync.value = await api.autosyncStatus()
  } catch {
    autosync.value = null
  }
}

async function setAutosync(enabled) {
  await save({ autosync_enabled: enabled })
  await loadAutosync()
}

async function setAutosyncDouyinCollect(enabled) {
  await save({ autosync_douyin_collect: enabled })
  await loadAutosync()
}

async function setAutosyncDecrypt(enabled) {
  await save({ autosync_wechat_decrypt: enabled })
  await loadAutosync()
}

async function setAutosyncInterval(value) {
  await save({ autosync_interval_minutes: Number(value) })
  await loadAutosync()
}

async function runAutosync() {
  syncBusy.value = true
  try {
    const res = await api.autosyncRun()
    syncJobId.value = res.job.id
    toast('已开始增量更新', 'ok')
  } catch (err) {
    toast(err.message, 'err')
  } finally {
    syncBusy.value = false
  }
}

async function onSyncDone() {
  syncJobId.value = ''
  await loadAutosync()
  await refreshStats()
}

function fmtTs(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString() : '—'
}

// ── 语音识别 ──
const asr = ref(null)
const themeLabel = computed(() =>
  store.themeSlug === 'system'
    ? `跟随系统（${currentTheme().dark ? '深色' : '浅色'}）`
    : currentTheme().name
)
const asrForm = ref({
  engine: 'off',
  auto_after_import: false,
  douyin_fallback: true,
  concurrency: 2,
  whisper_model: 'small',
  whisper_model_dir: '',
  whisper_endpoint: '',
  tencent_secret_id: '',
  tencent_secret_key: '',
  tencent_region: 'ap-shanghai',
})
const asrSaving = ref(false)

// ── 更新检查 ──
const appVersion = ref('')
const updateUrl = ref('')
const updateResult = ref(null)
const checkingUpdate = ref(false)

async function loadVersion() {
  try {
    const v = await api.version()
    appVersion.value = v.version
  } catch {
    appVersion.value = health.value?.version || ''
  }
  updateUrl.value = store.settings.update_url || ''
}

async function checkUpdate() {
  checkingUpdate.value = true
  updateResult.value = null
  try {
    if (updateUrl.value !== (store.settings.update_url || '')) {
      await save({ update_url: updateUrl.value })
    }
    updateResult.value = await api.updateCheck(updateUrl.value || undefined)
  } catch (err) {
    updateResult.value = { checked: true, error: err.message }
  } finally {
    checkingUpdate.value = false
  }
}
const asrTesting = ref(false)
const asrMessage = ref('')

async function setLang(value) {
  i18n.lang = value
  setLanguage(value)
  await save({ language: value })
}

async function loadAsr() {
  try {
    asr.value = await api.asrStatus()
    const engines = asr.value.engines
    asrForm.value.engine = engines.current || 'off'
    asrForm.value.auto_after_import = !!engines.auto
    asrForm.value.douyin_fallback = engines.douyin_fallback !== false
    asrForm.value.concurrency = engines.concurrency || 2
    asrForm.value.whisper_model = engines.local?.model || 'small'
    asrForm.value.whisper_endpoint = asr.value.engines?.local?.endpoint || ''
    asrForm.value.tencent_region = asr.value.secrets?.tencent?.region || 'ap-shanghai'
  } catch (err) {
    asrMessage.value = `读取识别配置失败：${err.message}`
  }
}

function asrBody() {
  const body = {
      engine: asrForm.value.engine,
      auto_after_import: asrForm.value.auto_after_import,
      asr_douyin_fallback: asrForm.value.douyin_fallback,
      concurrency: Number(asrForm.value.concurrency) || 2,
      whisper_model: asrForm.value.whisper_model,
      whisper_model_dir: asrForm.value.whisper_model_dir,
      whisper_endpoint: asrForm.value.whisper_endpoint,
      tencent_region: asrForm.value.tencent_region,
    }
  // 留空表示不修改已保存的密钥
  if (asrForm.value.tencent_secret_id) body.tencent_secret_id = asrForm.value.tencent_secret_id
  if (asrForm.value.tencent_secret_key) body.tencent_secret_key = asrForm.value.tencent_secret_key
  return body
}

async function saveAsr() {
  asrSaving.value = true
  asrMessage.value = ''
  try {
    const res = await api.saveAsrConfig(asrBody())
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
    // 刚输入的密钥还没点保存时，先落盘再测试；否则测试读不到
    const sid = asrForm.value.tencent_secret_id.trim()
    const skey = asrForm.value.tencent_secret_key.trim()
    if (sid || skey) {
      if (!sid || !skey) {
        asrMessage.value = '请同时填写 SecretId 和 SecretKey'
        return
      }
      await api.saveAsrConfig(asrBody())
      asrForm.value.tencent_secret_id = ''
      asrForm.value.tencent_secret_key = ''
    }
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
  await loadAutosync()
  await loadIdentity()
  await loadVersion()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('设置') }}</h1>
      <p>所有设置只保存在本机 <code class="mono">data/settings.json</code>，不会上传。</p>
    </div>

    <div class="section">
      <h2>{{ t('外观') }}</h2>
      <div class="card">
        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">{{ t('当前外观：') }}{{ themeLabel }}</div>
            <div class="hint">
              内置 4 套 + StyleKit 151 套；配色、圆角、边框和阴影都会跟着变
            </div>
          </div>
          <button class="btn" @click="store.view = 'appearance'">
            <Icon name="sparkles" :size="14" /> {{ t('浏览外观库') }}
          </button>
        </div>

        <div class="divider" />

        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">{{ t('语言') }}</div>
            <div class="hint">中文 / English</div>
          </div>
          <div class="row" style="gap: 6px">
            <button
              class="chip"
              :class="{ active: (store.settings.language || 'zh') === 'zh' }"
              @click="setLang('zh')"
            >中文</button>
            <button
              class="chip"
              :class="{ active: store.settings.language === 'en' }"
              @click="setLang('en')"
            >English</button>
          </div>
        </div>

        <div class="divider" />

        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">{{ t('紧凑模式') }}</div>
            <div class="hint">{{ t('消息行距更小，一屏能看更多') }}</div>
          </div>
          <button class="switch" :class="{ on: store.settings.density === 'compact' }" @click="setDensity(store.settings.density === 'compact' ? 'comfortable' : 'compact')" />
        </div>
      </div>
    </div>

    <div class="section">
      <h2>{{ t('阅读') }}</h2>
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
      <h2>{{ t('语音识别') }}</h2>
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
          <label>{{ t('地域') }}</label>
          <input v-model="asrForm.tencent_region" class="input" placeholder="ap-shanghai" />
        </div>

        <div class="row wrap" style="gap: 8px">
          <button class="btn sm" :disabled="asrTesting" @click="testAsr">
            <span v-if="asrTesting" class="spinner" />
            <Icon v-else name="link" :size="13" /> {{ t('测试密钥') }}
          </button>
          <button class="btn sm ghost danger" @click="clearAsrKeys">{{ t('清除密钥') }}</button>
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
            <label>{{ t('模型大小') }}</label>
            <select v-model="asrForm.whisper_model" class="select">
              <option value="base">base（最快，准确率一般）</option>
              <option value="small">small（推荐）</option>
              <option value="medium">medium（更准，更慢）</option>
            </select>
          </div>
          <div class="field" style="margin-bottom: 0">
            <label>{{ t('模型目录（可选）') }}</label>
            <input v-model="asrForm.whisper_model_dir" class="input" placeholder="留空用默认缓存目录" />
          </div>
          <div class="field" style="margin-bottom: 0">
            <label>{{ t('模型下载镜像（可选）') }}</label>
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
            <div style="font-weight: 550">{{ t('导入后自动识别') }}</div>
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
            <div style="font-weight: 550">{{ t('抖音语音兜底') }}</div>
            <div class="hint">抖音原生识别失败时，导入后用上面的引擎补转写</div>
          </div>
          <button
            class="switch"
            :class="{ on: asrForm.douyin_fallback }"
            @click="asrForm.douyin_fallback = !asrForm.douyin_fallback"
          />
        </div>

        <div class="row mt12">
          <div class="grow">
            <div style="font-weight: 550">{{ t('云端并发') }}</div>
            <div class="hint">最高 100；太大容易触发腾讯云限流。本地识别固定串行</div>
          </div>
          <select v-model.number="asrForm.concurrency" class="select" style="max-width: 110px">
            <option :value="1">1</option>
            <option :value="2">2</option>
            <option :value="4">4</option>
            <option :value="10">10</option>
            <option :value="25">25</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
          </select>
        </div>

        <div class="row mt20" style="gap: 10px">
          <button class="btn primary" :disabled="asrSaving" @click="saveAsr">
            <span v-if="asrSaving" class="spinner" />
            {{ t('保存') }}
          </button>
          <span v-if="asrMessage" class="hint">{{ asrMessage }}</span>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>我的身份</h2>
      <div class="card" v-if="identity">
        <div class="hint" style="margin-bottom: 4px">
          聊天界面的「我」由这里决定。微信一般能按登录账号自动识别；抖音是按私聊推断的。
          <b>如果导入的是别人导出的 chat.db，请把「我」改成你自己。</b>
        </div>
        <div
          v-for="key in ['wechat', 'douyin']"
          :key="key"
          class="row mt12"
          v-show="identity.platforms[key]?.message_count"
        >
          <div class="grow">
            <div style="font-weight: 550">
              {{ key === 'wechat' ? '微信' : '抖音' }}
              <span class="hint" style="font-weight: 400">
                · {{ selfSource(identity.platforms[key]) }}
              </span>
            </div>
            <div class="hint" v-if="identity.platforms[key].uid">
              当前：{{ identity.platforms[key].name }}（{{ identity.platforms[key].uid }}）
            </div>
            <div class="hint" v-else>还没有识别到「我」，请选择：</div>
          </div>
          <select v-model="selfPick[key]" class="select" style="max-width: 260px">
            <option value="">选择「我」…</option>
            <option
              v-for="c in identity.platforms[key].candidates"
              :key="c.uid"
              :value="c.uid"
            >{{ c.name }}（{{ c.message_count }} 条）</option>
            <option
              v-if="identity.platforms[key].uid
                && !identity.platforms[key].candidates.some((c) => c.uid === identity.platforms[key].uid)"
              :value="identity.platforms[key].uid"
            >{{ identity.platforms[key].name }}</option>
          </select>
          <button
            class="btn sm"
            :disabled="!selfPick[key]
              || (selfPick[key] === identity.platforms[key].uid
                  && !identity.platforms[key].needs_confirm)"
            @click="saveSelf(key)"
          >{{ identity.platforms[key].needs_confirm
                && selfPick[key] === identity.platforms[key].uid ? '确认是我' : '保存' }}</button>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>自动增量更新</h2>
      <div class="card">
        <div class="row">
          <div class="grow">
            <div style="font-weight: 550">定时导入新消息</div>
            <div class="hint">
              可先自动解密微信；抖音会先打开浏览器按「采集范围」采集，再按同一
              范围增量导入。自动采集失败时会继续导入已有引擎库。
            </div>
          </div>
          <button
            class="switch"
            :class="{ on: !!store.settings.autosync_enabled }"
            @click="setAutosync(!store.settings.autosync_enabled)"
          />
        </div>

        <div class="row mt12">
          <div class="grow">
            <div style="font-weight: 550">抖音先自动采集</div>
            <div class="hint">会弹出浏览器，需要扫码登录；失败会继续导入已有数据</div>
          </div>
          <button
            class="switch"
            :class="{ on: !!store.settings.autosync_douyin_collect }"
            @click="setAutosyncDouyinCollect(!store.settings.autosync_douyin_collect)"
          />
        </div>

        <div class="row mt12">
          <div class="grow">
            <div style="font-weight: 550">微信先自动解密</div>
            <div class="hint">需要微信正在运行并登录；失败会跳过解密、继续导入已有数据</div>
          </div>
          <button
            class="switch"
            :class="{ on: !!store.settings.autosync_wechat_decrypt }"
            @click="setAutosyncDecrypt(!store.settings.autosync_wechat_decrypt)"
          />
        </div>

        <div class="row mt12">
          <div class="grow">
            <div style="font-weight: 550">更新间隔</div>
            <div class="hint">开启后按这个间隔在后台检查新消息</div>
          </div>
          <select
            class="select"
            style="max-width: 140px"
            :value="store.settings.autosync_interval_minutes || 30"
            @change="setAutosyncInterval($event.target.value)"
          >
            <option :value="5">5 分钟</option>
            <option :value="15">15 分钟</option>
            <option :value="30">30 分钟</option>
            <option :value="60">60 分钟</option>
            <option :value="180">3 小时</option>
          </select>
        </div>

        <div class="row mt12">
          <div class="grow">
            <div class="hint">
              上次更新：{{ fmtTs(autosync?.last_run) }}
              <template v-if="autosync?.next_run"> · 下次约 {{ fmtTs(autosync.next_run) }}</template>
              <template v-if="autosync && !autosync.sources_available"> · 本机暂无可更新的数据源</template>
            </div>
          </div>
          <button class="btn sm" :disabled="syncBusy" @click="runAutosync">
            <span v-if="syncBusy" class="spinner" /> 立即更新
          </button>
        </div>

        <div v-if="syncJobId" class="mt12">
          <JobPanel :job-id="syncJobId" title="增量更新" @done="onSyncDone" />
        </div>
      </div>
    </div>

    <div class="section">
      <h2>{{ t('数据') }}</h2>
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
      <h2>{{ t('关于') }}</h2>
      <div class="card">
        <div class="row" style="margin-bottom: 14px">
          <div class="rail-logo" style="margin: 0; width: 42px; height: 42px">聊</div>
          <div class="grow">
            <div style="font-weight: 600">聊天信息工作台</div>
            <div class="hint">把微信与抖音的聊天记录合并成一条可浏览、可导出的时间线</div>
          </div>
          <span class="chip">v{{ appVersion || health?.version || '0.1.0' }}</span>
        </div>

        <div class="kv"><span>许可证</span><span>AGPL-3.0</span></div>
        <div class="kv"><span>微信解密组件</span><span>SIWX（AGPL-3.0）</span></div>
        <div class="kv"><span>抖音采集组件</span><span>douyin-chat-export（MIT）</span></div>

        <div class="divider" />

        <div class="row" style="gap: 8px; margin-bottom: 10px">
          <div class="grow">
            <div style="font-weight: 550">检查更新</div>
            <div class="hint">填 GitHub Release API 或自定义 JSON（{{ '{' }}version, notes, url{{ '}' }}）</div>
          </div>
          <input
            v-model="updateUrl"
            class="input"
            style="max-width: 320px"
            placeholder="https://api.github.com/repos/…/releases/latest"
          />
          <button class="btn sm" :disabled="checkingUpdate" @click="checkUpdate">
            <span v-if="checkingUpdate" class="spinner" />
            <Icon v-else name="refresh" :size="13" /> 检查
          </button>
        </div>

        <div v-if="updateResult" class="hint" style="margin-bottom: 10px">
          <template v-if="!updateResult.checked">未检查：{{ updateResult.reason }}</template>
          <template v-else-if="updateResult.error">检查失败：{{ updateResult.error }}</template>
          <template v-else-if="updateResult.available">
            有新版本 {{ updateResult.latest }}（当前 {{ updateResult.current }}）。
            <a v-if="updateResult.url" :href="updateResult.url" target="_blank" rel="noopener">去下载</a>
          </template>
          <template v-else>已经是最新版本（{{ updateResult.current }}）</template>
          <div v-if="updateResult.notes" class="mt12" style="white-space: pre-wrap">{{ updateResult.notes }}</div>
        </div>

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
