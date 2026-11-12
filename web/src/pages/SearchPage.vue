<script setup>
import { computed, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, openThreadAt } from '../lib/store'
import { fmtCount, fmtTime, PLATFORM_META, kindLabel } from '../lib/format'
import Icon from '../components/Icon.vue'

const q = ref('')
const platform = ref('')
const kinds = ref([])
const sender = ref('')
const rangeKey = ref('all')
const customFrom = ref('')
const customTo = ref('')
const grouped = ref(false)

const items = ref([])
const groups = ref([])
const total = ref(0)
const offset = ref(0)
const engine = ref('')
const loading = ref(false)
const searched = ref(false)
const PAGE = 50

const KIND_OPTIONS = [
  ['text', '文本'], ['voice', '语音'], ['image', '图片'], ['video', '视频'],
  ['sticker', '表情'], ['file', '文件'], ['forward', '转发'], ['share', '链接'],
  ['miniprogram', '小程序'], ['location', '位置'], ['card', '名片'], ['revoke', '撤回'],
]

function tsOf(date) {
  if (!date) return null
  const ts = Math.floor(new Date(`${date}T00:00:00`).getTime() / 1000)
  return Number.isFinite(ts) ? ts : null
}

function range() {
  const now = Math.floor(Date.now() / 1000)
  if (rangeKey.value === '7') return [now - 7 * 86400, null]
  if (rangeKey.value === '30') return [now - 30 * 86400, null]
  if (rangeKey.value === '365') return [now - 365 * 86400, null]
  if (rangeKey.value === 'custom') return [tsOf(customFrom.value), tsOf(customTo.value) ? tsOf(customTo.value) + 86400 : null]
  return [null, null]
}

function toggleKind(key) {
  const i = kinds.value.indexOf(key)
  if (i >= 0) kinds.value.splice(i, 1)
  else kinds.value.push(key)
}

async function search(reset = true) {
  const query = q.value.trim()
  if (!query) {
    toast('输入关键词再搜索', 'err')
    return
  }
  if (reset) offset.value = 0
  loading.value = true
  const [since, until] = range()
  try {
    const res = await api.search({
      q: query,
      platform: platform.value || undefined,
      kinds: kinds.value.join(',') || undefined,
      sender: sender.value.trim() || undefined,
      sinceTs: since || undefined,
      untilTs: until || undefined,
      limit: PAGE,
      offset: offset.value,
      group: grouped.value,
    })
    items.value = res.items || []
    groups.value = res.groups || []
    total.value = res.total || 0
    engine.value = res.engine || ''
    searched.value = true
  } catch (err) {
    toast(`搜索失败：${err.message}`, 'err')
  } finally {
    loading.value = false
  }
}

function nextPage() {
  if (offset.value + PAGE >= total.value) return
  offset.value += PAGE
  search(false)
}

function prevPage() {
  if (offset.value === 0) return
  offset.value = Math.max(0, offset.value - PAGE)
  search(false)
}

function open(item) {
  openThreadAt(item.thread_id, item.ts)
}

const rangeText = computed(() => ({
  all: '全部时间', '7': '最近 7 天', '30': '最近 30 天', '365': '最近一年', custom: '自定义',
}[rangeKey.value]))

function snippet(item) {
  const text = (item.transcript || item.text || '').replace(/\s+/g, ' ').trim()
  return text.length > 120 ? `${text.slice(0, 120)}…` : text
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>搜索</h1>
      <p>
        全文检索所有会话（含语音转写）。短词走模糊匹配，3 个字以上走 FTS 索引。
      </p>
    </div>

    <div class="card">
      <div class="row" style="gap: 8px">
        <div class="search-box grow" style="max-width: none">
          <Icon name="search" :size="15" />
          <input
            v-model="q"
            placeholder="搜索消息内容，回车开始"
            @keyup.enter="search(true)"
          />
        </div>
        <button class="btn primary" :disabled="loading" @click="search(true)">
          <span v-if="loading" class="spinner" />
          <Icon v-else name="search" :size="14" /> 搜索
        </button>
      </div>

      <div class="filters mt12">
        <div class="filter-row">
          <span class="hint" style="width: 52px">平台</span>
          <button class="chip" :class="{ active: platform === '' }" @click="platform = ''">全部</button>
          <button class="chip" :class="{ active: platform === 'wechat' }" @click="platform = 'wechat'">微信</button>
          <button class="chip" :class="{ active: platform === 'douyin' }" @click="platform = 'douyin'">抖音</button>
        </div>

        <div class="filter-row">
          <span class="hint" style="width: 52px">类型</span>
          <button
            v-for="[key, label] in KIND_OPTIONS"
            :key="key"
            class="chip"
            :class="{ active: kinds.includes(key) }"
            @click="toggleKind(key)"
          >
            {{ label }}
          </button>
        </div>

        <div class="filter-row">
          <span class="hint" style="width: 52px">时间</span>
          <select v-model="rangeKey" class="select" style="max-width: 130px">
            <option value="all">全部时间</option>
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="365">最近一年</option>
            <option value="custom">自定义…</option>
          </select>
          <template v-if="rangeKey === 'custom'">
            <input v-model="customFrom" type="date" class="input" style="max-width: 150px" />
            <span class="hint">至</span>
            <input v-model="customTo" type="date" class="input" style="max-width: 150px" />
          </template>
        </div>

        <div class="filter-row">
          <span class="hint" style="width: 52px">发送者</span>
          <input v-model="sender" class="input" style="max-width: 220px" placeholder="昵称（可留空）" />
          <button class="chip" :class="{ active: grouped }" @click="grouped = !grouped; searched && search(true)">
            按会话分组
          </button>
        </div>
      </div>
    </div>

    <div v-if="searched" class="mt20">
      <div class="row" style="margin-bottom: 10px">
        <span class="grow">
          <strong>{{ fmtCount(total) }}</strong> 条结果
          <span class="hint" style="margin-left: 8px">
            {{ rangeText }} · {{ engine === 'fts' ? 'FTS 索引' : '模糊匹配' }}
          </span>
        </span>
        <span class="hint" v-if="!grouped && total">
          第 {{ total ? offset + 1 : 0 }}–{{ Math.min(offset + items.length, total) }} 条
        </span>
      </div>

      <!-- 按会话分组 -->
      <div v-if="grouped" class="group-list">
        <button
          v-for="g in groups"
          :key="g.thread_id"
          class="group-row"
          @click="open({ thread_id: g.thread_id, ts: g.last_ts })"
        >
          <span class="plat-dot" :class="PLATFORM_META[g.platform]?.cls">
            {{ PLATFORM_META[g.platform]?.label }}
          </span>
          <span class="grow g-title">{{ g.title }}</span>
          <span v-if="g.kind === 'group'" class="group-tag">群</span>
          <span class="strong">{{ fmtCount(g.n) }}</span>
          <span class="hint">{{ fmtTime(g.last_ts) }}</span>
        </button>
        <div v-if="!groups.length" class="empty" style="height: 160px">
          <p>没有匹配的会话</p>
        </div>
      </div>

      <!-- 逐条结果 -->
      <div v-else class="hit-list">
        <button v-for="item in items" :key="item.id" class="hit-row" @click="open(item)">
          <span class="plat-dot" :class="PLATFORM_META[item.platform]?.cls">
            {{ PLATFORM_META[item.platform]?.label }}
          </span>
          <span class="hit-main">
            <span class="hit-top">
              <span class="hit-conv">{{ item.conv_title || '未命名' }}</span>
              <span class="hint">{{ item.sender_name || item.sender_uid }}</span>
              <span class="kind-tag">{{ kindLabel(item.kind) || item.kind }}</span>
            </span>
            <span class="hit-text">{{ snippet(item) || '（无文本）' }}</span>
          </span>
          <span class="hint nowrap">{{ fmtTime(item.ts) }}</span>
        </button>

        <div v-if="!items.length" class="empty" style="height: 160px">
          <div>
            <div class="icon"><Icon name="search" :size="22" /></div>
            <p>没有找到匹配的消息</p>
          </div>
        </div>
      </div>

      <div v-if="!grouped && total > PAGE" class="row" style="justify-content: center; margin-top: 16px; gap: 10px">
        <button class="btn sm" :disabled="offset === 0 || loading" @click="prevPage">上一页</button>
        <span class="hint">{{ Math.floor(offset / PAGE) + 1 }} / {{ Math.ceil(total / PAGE) }}</span>
        <button class="btn sm" :disabled="offset + PAGE >= total || loading" @click="nextPage">下一页</button>
      </div>
    </div>

    <div v-else class="empty" style="height: 260px">
      <div>
        <div class="icon"><Icon name="search" :size="24" /></div>
        <h3>搜索所有聊天记录</h3>
        <p>支持按平台、消息类型、时间范围、发送者筛选，也可以按会话分组。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; flex-direction: column; gap: 8px; }
.filter-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.group-list, .hit-list { display: flex; flex-direction: column; gap: 6px; }
.group-row, .hit-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  text-align: left;
  padding: 9px 12px;
  border: var(--border-w, 1px) solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text);
  cursor: pointer;
}
.group-row:hover, .hit-row:hover { border-color: var(--accent); }
.g-title { font-weight: 600; }
.strong { font-weight: 600; }
.hit-main { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.hit-top { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.hit-conv { font-weight: 600; }
.kind-tag {
  font-size: 10.5px;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--panel-3);
  color: var(--text-dim);
}
.hit-text {
  font-size: 13px;
  color: var(--text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
