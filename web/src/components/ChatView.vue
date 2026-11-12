<script setup>
import { computed, ref, watch, nextTick, onMounted } from 'vue'
import { api } from '../lib/api'
import { fmtDay, fmtCount, PLATFORM_META, initial, avatarColor, fmtTime } from '../lib/format'
import Icon from './Icon.vue'
import MessageBubble from './MessageBubble.vue'
import MediaPanel from './MediaPanel.vue'

const props = defineProps({
  thread: { type: Object, default: null },
  messages: { type: Array, default: () => [] },
  hasMoreBefore: { type: Boolean, default: false },
  platform: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['load-older', 'toggle-info', 'update:platform', 'export', 'open-image', 'jump-to'])

const scroller = ref(null)
const searchOpen = ref(false)
const searchQuery = ref('')
const searchResults = ref([])
const searchTotal = ref(0)
const searching = ref(false)
const mediaOpen = ref(false)
const calendarOpen = ref(false)
const calendarLoading = ref(false)
const dayList = ref([])
const prevHeight = ref(0)
const pendingRestore = ref(false)
const firstLoad = ref(true)

const info = computed(() => props.thread)
const platforms = computed(() => info.value?.platforms || [])
const isGroup = computed(() => {
  if (!info.value) return false
  return info.value.thread_kind === 'conv'
    ? info.value.conversation?.kind === 'group'
    : false
})
const title = computed(() =>
  info.value?.thread_kind === 'contact'
    ? info.value.contact?.display_name
    : info.value?.conversation?.title || ''
)
const subtitle = computed(() => {
  if (!info.value) return ''
  const total = props.messages.length
  const parts = []
  if (isGroup.value) parts.push('群聊')
  if (info.value.thread_kind === 'contact' && info.value.merged) parts.push('跨平台合并')
  parts.push(`${fmtCount(info.value.thread_kind === 'contact' ? info.value.conversations?.reduce((s, c) => s + (c.msg_count || 0), 0) : info.value.conversation?.msg_count || total)} 条`)
  return parts.join(' · ')
})

const avatarText = computed(() => initial(title.value))
const avatarBg = computed(() => avatarColor(info.value?.thread_id || title.value))
const avatarUrl = computed(() => {
  if (!info.value) return ''
  if (info.value.thread_kind === 'contact') return info.value.contact?.avatar_url || ''
  return info.value.conversation?.avatar_url || ''
})
const mediaUrl = (p) => (p ? `/media/${p}` : '')

// 把消息按天切段，渲染日期分隔
const dayGroups = computed(() => {
  const groups = []
  let current = null
  for (const msg of props.messages) {
    const day = fmtDay(msg.ts)
    if (!current || current.day !== day) {
      current = { day, ts: msg.ts, items: [] }
      groups.push(current)
    }
    current.items.push(msg)
  }
  return groups
})

function onScroll() {
  const el = scroller.value
  if (!el) return
  if (el.scrollTop < 80 && props.hasMoreBefore && !props.loading) {
    prevHeight.value = el.scrollHeight
    pendingRestore.value = true
    emit('load-older')
  }
}

function scrollToBottom(smooth = false) {
  const el = scroller.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    const el = scroller.value
    if (!el) return
    if (firstLoad.value && props.messages.length) {
      scrollToBottom()
      firstLoad.value = false
    } else if (pendingRestore.value) {
      el.scrollTop = el.scrollHeight - prevHeight.value
      pendingRestore.value = false
    }
  }
)

watch(
  () => props.thread?.thread_id,
  async () => {
    searchOpen.value = false
    searchResults.value = []
    firstLoad.value = true
    await nextTick()
  }
)

onMounted(() => nextTick(() => scrollToBottom()))

async function runSearch() {
  const q = searchQuery.value.trim()
  if (!q) return
  searching.value = true
  try {
    const res = await api.search({
      q,
      threadId: props.thread.thread_id,
      platform: props.platform || undefined,
      limit: 60,
    })
    searchResults.value = res.items
    searchTotal.value = res.total
  } catch (err) {
    searchResults.value = []
  } finally {
    searching.value = false
  }
}

const calendarGroups = computed(() => {
  const map = new Map()
  // 最近的在最前面
  for (const d of [...dayList.value].reverse()) {
    const month = d.day.slice(0, 7)
    if (!map.has(month)) map.set(month, [])
    map.get(month).push(d)
  }
  return [...map.entries()]
})

async function toggleCalendar() {
  calendarOpen.value = !calendarOpen.value
  if (!calendarOpen.value || dayList.value.length || !info.value) return
  calendarLoading.value = true
  try {
    const res = await api.days(info.value.thread_id, props.platform || undefined)
    dayList.value = res.items || []
  } catch (err) {
    /* 日期列表失败不阻断阅读 */
  } finally {
    calendarLoading.value = false
  }
}

function jumpToDay(day) {
  calendarOpen.value = false
  const ts = Math.floor(new Date(`${day}T00:00:00+08:00`).getTime() / 1000)
  if (Number.isFinite(ts)) emit('jump-to', { ts })
}

function jumpTo(item) {
  searchOpen.value = false
  emit('jump-to', { ts: item.ts, msgId: item.id })
}

function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (searchOpen.value) nextTick(() => document.getElementById('thread-search')?.focus())
}
</script>

<template>
  <section class="chat">
    <template v-if="!info">
      <div class="empty">
        <div>
          <div class="icon"><Icon name="chat" :size="26" /></div>
          <h3>选择左边的一个会话</h3>
          <p>微信和抖音的消息会按时间交错在同一条时间线上；把同一个人的两个平台账号合并后，就能一起看。</p>
        </div>
      </div>
    </template>

    <template v-else>
      <header class="chat-head">
        <div class="avatar" :style="{ background: avatarBg + '26', color: avatarBg }">
          <img v-if="avatarUrl" :src="mediaUrl(avatarUrl)" alt="" />
          <template v-else>{{ avatarText }}</template>
        </div>
        <div class="chat-title">
          <h2>{{ title }}</h2>
          <span
            v-for="p in platforms"
            :key="p"
            class="plat-dot"
            :class="PLATFORM_META[p]?.cls"
          >{{ PLATFORM_META[p]?.label || p }}</span>
          <span class="group-tag" v-if="isGroup">群</span>
        </div>
        <span class="chat-sub">{{ subtitle }}</span>

        <div class="chat-actions">
          <div v-if="platforms.length > 1" class="row" style="gap: 4px; margin-right: 4px">
            <button class="chip" :class="{ active: !platform }" @click="emit('update:platform', '')">全部</button>
            <button
              v-for="p in platforms"
              :key="p"
              class="chip"
              :class="[`plat-${p}`, { active: platform === p }]"
              @click="emit('update:platform', p)"
            >{{ PLATFORM_META[p]?.label }}</button>
          </div>
          <button class="icon-btn" :class="{ on: mediaOpen }" title="媒体库" @click="mediaOpen = true">
            <Icon name="image" :size="17" />
          </button>
          <button class="icon-btn" :class="{ on: calendarOpen }" title="按日期跳转" @click="toggleCalendar">
            <Icon name="calendar" :size="17" />
          </button>
          <button class="icon-btn" :class="{ on: searchOpen }" title="在会话内查找" @click="toggleSearch">
            <Icon name="search" :size="17" />
          </button>
          <button class="icon-btn" title="导出这个会话" @click="emit('export')">
            <Icon name="export" :size="17" />
          </button>
          <button class="icon-btn" title="会话信息" @click="emit('toggle-info')">
            <Icon name="panel" :size="17" />
          </button>
        </div>
      </header>

      <MediaPanel
        v-if="mediaOpen"
        :thread-id="info.thread_id"
        @close="mediaOpen = false"
        @open-image="emit('open-image', $event)"
      />

      <div v-if="calendarOpen" class="cal-pop">
        <div class="cal-head">
          <span class="grow">按日期跳转</span>
          <button class="icon-btn" style="width: 22px; height: 22px" @click="calendarOpen = false">
            <Icon name="close" :size="13" />
          </button>
        </div>
        <div v-if="calendarLoading" class="hint" style="padding: 8px 4px">读取日期…</div>
        <div v-else class="cal-body">
          <div v-for="[month, days] in calendarGroups" :key="month" class="cal-month">
            <div class="cal-month-title">{{ month }}</div>
            <div class="cal-days">
              <button
                v-for="d in days"
                :key="d.day"
                class="cal-day"
                :title="`${d.day} · ${d.n} 条`"
                @click="jumpToDay(d.day)"
              >
                {{ Number(d.day.slice(8)) }}
                <span class="faint">{{ d.n }}</span>
              </button>
            </div>
          </div>
          <div v-if="!calendarGroups.length" class="hint" style="padding: 8px 4px">没有日期数据</div>
        </div>
      </div>

      <div v-if="searchOpen" class="search-pop">
        <div class="search-box">
          <Icon name="search" :size="14" />
          <input
            id="thread-search"
            v-model="searchQuery"
            placeholder="在这个会话里搜索…"
            @keyup.enter="runSearch"
          />
          <button class="icon-btn" style="width: 24px; height: 24px" @click="searchOpen = false">
            <Icon name="close" :size="14" />
          </button>
        </div>
        <div v-if="searching" class="hint" style="padding: 10px 4px">搜索中…</div>
        <div v-else-if="searchQuery && searchResults.length === 0" class="hint" style="padding: 10px 4px">
          没有找到
        </div>
        <div v-else-if="searchResults.length" class="search-results">
          <div class="hint" style="padding: 6px 4px">{{ searchTotal }} 条结果</div>
          <button
            v-for="r in searchResults"
            :key="r.id"
            class="search-hit"
            @click="jumpTo(r)"
          >
            <span class="plat-dot" :class="PLATFORM_META[r.platform]?.cls">{{ PLATFORM_META[r.platform]?.label }}</span>
            <span class="t">{{ r.text }}</span>
            <span class="d">{{ fmtDay(r.ts).slice(5) }}</span>
          </button>
        </div>
      </div>

      <div ref="scroller" class="messages" @scroll.passive="onScroll">
        <div v-if="hasMoreBefore" class="load-more">
          <span v-if="loading" class="spinner" />
          <span v-else class="hint">向上滚动加载更早的消息</span>
        </div>

        <template v-for="group in dayGroups" :key="group.day">
          <div class="day-sep">{{ group.day }}</div>
          <MessageBubble
            v-for="msg in group.items"
            :key="msg.id"
            :message="msg"
            :show-sender="isGroup"
            @open-image="emit('open-image', $event)"
          />
        </template>

        <div v-if="!messages.length" class="empty" style="height: auto; padding: 60px 0">
          <div>
            <div class="icon"><Icon name="database" :size="24" /></div>
            <h3>这个会话还没有消息</h3>
            <p>可能是导入时被过滤了，或者该账号的消息还没同步。</p>
          </div>
        </div>
      </div>

      <button v-if="messages.length" class="jump-bottom" title="回到最新" @click="scrollToBottom(true)">
        <Icon name="arrowDown" :size="17" />
      </button>
    </template>
  </section>
</template>

<style scoped>
.search-pop {
  position: relative;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  padding: 12px 18px 10px;
  z-index: 4;
}
.search-results { max-height: 260px; overflow-y: auto; }
.search-hit {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 8px;
  align-items: center;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  padding: 7px 8px;
  border-radius: 8px;
  color: var(--text-dim);
}
.search-hit:hover { background: var(--panel-2); color: var(--text); }
.search-hit .t {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
}
.search-hit .d { font-size: 11px; color: var(--text-faint); }
.load-more {
  display: grid;
  place-items: center;
  padding: 4px 0 14px;
  min-height: 28px;
}
.avatar img { width: 100%; height: 100%; object-fit: cover; }


.chat { position: relative; }
.cal-pop {
  position: absolute;
  top: 62px;
  right: 76px;
  width: 322px;
  max-height: 420px;
  overflow: auto;
  background: var(--panel);
  border: var(--border-w, 1px) solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 10px;
  z-index: 30;
}
.cal-head { display: flex; align-items: center; margin-bottom: 8px; font-weight: 600; font-size: 13px; }
.cal-month { margin-bottom: 10px; }
.cal-month-title { font-size: 11px; color: var(--text-faint); margin-bottom: 4px; }
.cal-days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.cal-day {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 4px 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: var(--panel-2);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}
.cal-day:hover { border-color: var(--accent); }
.cal-day .faint { font-size: 9px; }
</style>
