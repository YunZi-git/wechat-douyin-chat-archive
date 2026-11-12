<script setup>
import { computed } from 'vue'
import { fmtCount, fmtRelative, previewText, initial, avatarColor, PLATFORM_META } from '../lib/format'
import Icon from './Icon.vue'

const props = defineProps({
  threads: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  platform: { type: String, default: '' },
  query: { type: String, default: '' },
  activeId: { type: String, default: '' },
  mergedOnly: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'update:platform', 'update:query', 'update:merged-only', 'refresh'])

const mediaUrl = (p) => (p ? `/media/${p}` : '')

const visible = computed(() => {
  if (!props.mergedOnly) return props.threads
  return props.threads.filter((t) => (t.platforms || []).length > 1)
})

const counts = computed(() => {
  let wechat = 0
  let douyin = 0
  let merged = 0
  for (const t of props.threads) {
    const ps = t.platforms || []
    if (ps.includes('wechat')) wechat += 1
    if (ps.includes('douyin')) douyin += 1
    if (ps.length > 1) merged += 1
  }
  return { wechat, douyin, merged }
})
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-title">
        <div class="row" style="gap: 8px">
          <h2>会话</h2>
          <span>{{ total }}</span>
        </div>
        <button class="icon-btn" style="width: 28px; height: 28px" title="刷新" @click="emit('refresh')">
          <Icon name="refresh" :size="15" />
        </button>
      </div>

      <div class="search-box">
        <Icon name="search" :size="14" />
        <input
          :value="query"
          placeholder="搜索联系人或会话…"
          @input="emit('update:query', $event.target.value)"
        />
        <button v-if="query" class="icon-btn" style="width: 22px; height: 22px" @click="emit('update:query', '')">
          <Icon name="close" :size="13" />
        </button>
      </div>
    </div>

    <div class="filter-row">
      <button class="chip" :class="{ active: !platform }" @click="emit('update:platform', '')">
        全部 {{ threads.length }}
      </button>
      <button class="chip plat-wechat" :class="{ active: platform === 'wechat' }" @click="emit('update:platform', 'wechat')">
        微信 {{ counts.wechat }}
      </button>
      <button class="chip plat-douyin" :class="{ active: platform === 'douyin' }" @click="emit('update:platform', 'douyin')">
        抖音 {{ counts.douyin }}
      </button>
      <button class="chip" :class="{ active: mergedOnly }" @click="emit('update:merged-only', !mergedOnly)">
        已合并 {{ counts.merged }}
      </button>
    </div>

    <div class="thread-list">
      <div v-if="loading" class="empty" style="height: 120px">
        <div class="row"><div class="spinner" /> <span class="hint">加载中…</span></div>
      </div>

      <div
        v-for="t in visible"
        :key="t.thread_id"
        class="thread-item"
        :class="{ active: t.thread_id === activeId }"
        @click="emit('select', t)"
      >
        <div class="avatar" :style="{ background: avatarColor(t.thread_id) + '24', color: avatarColor(t.thread_id) }">
          <img v-if="t.avatar" :src="mediaUrl(t.avatar)" alt="" />
          <template v-else>{{ initial(t.title) }}</template>
        </div>

        <div class="thread-main">
          <div class="thread-row1">
            <span class="thread-name">{{ t.title }}</span>
            <span
              v-for="p in t.platforms"
              :key="p"
              class="plat-dot"
              :class="PLATFORM_META[p]?.cls"
            >{{ PLATFORM_META[p]?.short || p }}</span>
            <span v-if="t.kind === 'group'" class="group-tag">群</span>
          </div>
          <div class="thread-preview">{{ previewText(t) }}</div>
        </div>

        <div class="thread-meta">
          <span>{{ fmtRelative(t.last_ts) }}</span>
          <span class="count-pill">{{ fmtCount(t.msg_count) }}</span>
        </div>
      </div>

      <div v-if="!loading && !visible.length" class="empty" style="height: 200px">
        <div>
          <div class="icon"><Icon name="search" :size="22" /></div>
          <h3>没有匹配的会话</h3>
          <p>换个关键词，或者清掉上面的筛选条件。</p>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.avatar img { width: 100%; height: 100%; object-fit: cover; }
</style>
