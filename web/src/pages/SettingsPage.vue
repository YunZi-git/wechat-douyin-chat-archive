<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, applyTheme, applyDensity, refreshStats } from '../lib/store'
import { fmtCount, fmtDateTime, fmtBytes } from '../lib/format'
import Icon from '../components/Icon.vue'

const THEMES = [
  { key: 'dark', label: '深色', swatch: ['#0d0f14', '#6c8cff'] },
  { key: 'light', label: '浅色', swatch: ['#f2f4f8', '#3f6bff'] },
  { key: 'warm', label: '暖棕', swatch: ['#17120e', '#e0a25c'] },
  { key: 'purple', label: '紫夜', swatch: ['#100d1a', '#9d7bff'] },
]

const saving = ref(false)
const clearing = ref(false)
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

async function setTheme(key) {
  applyTheme(key)
  await save({ theme: key })
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
        <div class="theme-grid">
          <button
            v-for="t in THEMES"
            :key="t.key"
            class="theme-card"
            :class="{ active: store.settings.theme === t.key }"
            @click="setTheme(t.key)"
          >
            <span class="swatch" :style="{ background: t.swatch[0], borderColor: t.swatch[1] }">
              <i :style="{ background: t.swatch[1] }" />
            </span>
            <span>{{ t.label }}</span>
            <Icon v-if="store.settings.theme === t.key" name="check" :size="14" />
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
.theme-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.theme-card {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 12px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text-dim);
  font-size: 12.5px;
}
.theme-card:hover { border-color: var(--text-faint); }
.theme-card.active { border-color: var(--accent); color: var(--text); }
.swatch {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  border: 2px solid;
  display: grid;
  place-items: center;
  flex: none;
}
.swatch i { width: 8px; height: 8px; border-radius: 3px; display: block; }
@media (max-width: 720px) {
  .theme-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
