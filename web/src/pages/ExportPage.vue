<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, downloadUrl } from '../lib/api'
import { toast } from '../lib/store'
import { fmtBytes, fmtCount, fmtDateTime, fmtRelative, initial, avatarColor, PLATFORM_META, previewText } from '../lib/format'
import { t } from '../lib/i18n'
import Icon from '../components/Icon.vue'

const threads = ref([])
const loading = ref(true)
const query = ref('')
const selected = ref(null)
const format = ref('jsonl')
const platform = ref('')
const busy = ref(false)
const result = ref(null)
const exportsList = ref([])

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return threads.value
  return threads.value.filter((t) => (t.title || '').toLowerCase().includes(q))
})

const platforms = computed(() => selected.value?.platforms || [])

async function load() {
  loading.value = true
  try {
    const [res, ex] = await Promise.all([api.threads({ limit: 400 }), api.exports()])
    threads.value = res.items
    exportsList.value = ex.items || []
  } catch (err) {
    toast(`读取失败：${err.message}`, 'err')
  } finally {
    loading.value = false
  }
}

function pick(t) {
  selected.value = t
  platform.value = ''
  result.value = null
}

async function run() {
  if (!selected.value) return
  busy.value = true
  result.value = null
  try {
    const res = await api.exportThread(selected.value.thread_id, format.value, platform.value || undefined)
    result.value = res
    exportsList.value = (await api.exports()).items || []
    toast('导出完成', 'ok')
  } catch (err) {
    toast(`导出失败：${err.message}`, 'err')
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>{{ t('导出') }}</h1>
      <p>导出成 ChatLab 格式（可以直接喂给分析工具）或可读的 Markdown。合并会话可以分别导出，也可以合成一份。</p>
    </div>

    <div class="grid two" style="align-items: start">
      <!-- 左：选会话 -->
      <div class="card" style="padding: 16px">
        <div class="search-box" style="margin-bottom: 12px">
          <Icon name="search" :size="14" />
          <input v-model="query" placeholder="找会话…" />
        </div>

        <div v-if="loading" class="row"><div class="spinner" /> <span class="hint">加载中…</span></div>
        <div v-else class="pick-list">
          <button
            v-for="t in filtered"
            :key="t.thread_id"
            class="pick-item"
            :class="{ active: selected?.thread_id === t.thread_id }"
            @click="pick(t)"
          >
            <div class="avatar" style="width: 32px; height: 32px; border-radius: 9px; font-size: 12px"
              :style="{ background: avatarColor(t.thread_id) + '24', color: avatarColor(t.thread_id) }">
              {{ initial(t.title) }}
            </div>
            <div class="row-main">
              <div class="row-title">{{ t.title }}</div>
              <div class="row-sub">{{ fmtCount(t.msg_count) }} 条 · {{ fmtRelative(t.last_ts) }}</div>
            </div>
            <span v-for="p in t.platforms" :key="p" class="plat-dot" :class="PLATFORM_META[p]?.cls">
              {{ PLATFORM_META[p]?.short }}
            </span>
          </button>
          <div v-if="!filtered.length" class="hint" style="padding: 20px 0; text-align: center">没有匹配的会话</div>
        </div>
      </div>

      <!-- 右：导出设置 -->
      <div>
        <div class="card">
          <template v-if="!selected">
            <div class="empty" style="height: 180px; padding: 0">
              <div>
                <div class="icon"><Icon name="export" :size="22" /></div>
                <h3>先从左边选一个会话</h3>
              </div>
            </div>
          </template>

          <template v-else>
            <div class="row" style="margin-bottom: 16px">
              <div class="avatar" style="background: var(--panel-2); color: var(--text-dim)">
                {{ initial(selected.title) }}
              </div>
              <div class="grow">
                <div style="font-weight: 600">{{ selected.title }}</div>
                <div class="faint small">{{ fmtCount(selected.msg_count) }} 条消息</div>
              </div>
            </div>

            <div class="field">
              <label>格式</label>
              <div class="rows">
                <button
                  v-for="f in [
                    { key: 'jsonl', label: 'ChatLab JSONL', hint: '逐行 JSON，大文件友好' },
                    { key: 'json', label: 'ChatLab JSON', hint: '完整 JSON，便于人工阅读' },
                    { key: 'markdown', label: 'Markdown', hint: '纯文本归档' },
                  ]"
                  :key="f.key"
                  class="row-card"
                  :class="{ selected: format === f.key }"
                  @click="format = f.key"
                >
                  <div class="checkbox" :class="{ on: format === f.key }"><Icon name="check" :size="12" /></div>
                  <div class="row-main">
                    <div class="row-title">{{ f.label }}</div>
                    <div class="row-sub">{{ f.hint }}</div>
                  </div>
                </button>
              </div>
            </div>

            <div v-if="platforms.length > 1" class="field">
              <label>范围</label>
              <select v-model="platform" class="select">
                <option value="">合并导出（微信 + 抖音）</option>
                <option v-for="p in platforms" :key="p" :value="p">只导出 {{ PLATFORM_META[p]?.label }}</option>
              </select>
            </div>

            <button class="btn primary block" :disabled="busy" @click="run">
              <span v-if="busy" class="spinner" />
              <Icon v-else name="export" :size="14" />
              {{ busy ? '导出中…' : '开始导出' }}
            </button>

            <div v-if="result" class="result-box">
              <div class="row">
                <Icon name="file" :size="15" />
                <span class="grow" style="overflow: hidden; text-overflow: ellipsis">{{ result.name }}</span>
                <span class="faint small">{{ fmtBytes(result.size) }}</span>
              </div>
              <a class="btn sm primary mt12" :href="downloadUrl(result.name)" download>
                <Icon name="import" :size="13" /> 下载
              </a>
            </div>
          </template>
        </div>

        <div class="card mt20">
          <div class="row" style="margin-bottom: 10px">
            <span class="grow" style="font-weight: 600">最近的导出</span>
            <button class="btn sm ghost" @click="load"><Icon name="refresh" :size="12" /></button>
          </div>
          <div v-if="!exportsList.length" class="hint">还没有导出过文件。</div>
          <div v-for="f in exportsList.slice(0, 10)" :key="f.name" class="row" style="padding: 6px 0; font-size: 12.5px">
            <Icon name="file" :size="13" />
            <a class="grow nowrap" :href="downloadUrl(f.name)" :title="f.name" style="overflow: hidden; text-overflow: ellipsis">
              {{ f.name }}
            </a>
            <span class="faint small nowrap">{{ fmtBytes(f.size) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pick-list { max-height: 560px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.pick-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 8px 10px;
  color: var(--text);
}
.pick-item:hover { background: var(--panel-2); }
.pick-item.active { background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent) 30%, transparent); }
.result-box {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  margin-top: 14px;
}
</style>
