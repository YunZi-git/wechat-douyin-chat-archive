<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../lib/api'
import { toast } from '../lib/store'
import { fmtBytes, fmtCount, fmtTime, kindLabel } from '../lib/format'
import Icon from './Icon.vue'
import JobPanel from './JobPanel.vue'

const props = defineProps({
  threadId: { type: String, required: true },
})
const emit = defineEmits(['close', 'open-image'])

const tab = ref('image')
const items = ref([])
const counts = ref({})
const total = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const playerUrl = ref('')
const jobId = ref('')
const broken = ref(new Set())

const TABS = [
  ['image', '图片'],
  ['sticker', '表情'],
  ['video', '视频'],
  ['file', '文件'],
]

const tabLabel = computed(() => TABS.find(([k]) => k === tab.value)?.[1] || '')

async function load(reset = true) {
  if (reset) items.value = []
  loading.value = true
  try {
    const offset = reset ? 0 : items.value.length
    const res = await api.threadMedia(props.threadId, { kind: tab.value, limit: 60, offset })
    items.value = reset ? res.items : [...items.value, ...res.items]
    counts.value = res.counts || {}
    total.value = res.total || 0
    hasMore.value = res.has_more
  } catch (err) {
    toast(`读取媒体失败：${err.message}`, 'err')
  } finally {
    loading.value = false
  }
}

function switchTab(key) {
  if (tab.value === key) return
  tab.value = key
  load(true)
}

function imageSrc(item) {
  if (item.media_url) return item.media_url
  return item.platform === 'wechat' ? `/api/messages/${item.id}/image` : ''
}

function onImageError(id) {
  broken.value = new Set(broken.value).add(id)
}

function openVideo(item) {
  playerUrl.value = item.media_url || `/api/messages/${item.id}/video`
}

async function prewarm() {
  try {
    const res = await api.mediaPrewarm({ thread_id: props.threadId, limit: 300 })
    jobId.value = res.job.id
    toast('开始缓存本会话媒体', 'ok')
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function onJobDone() {
  jobId.value = ''
  await load(true)
}

onMounted(() => load(true))
watch(() => props.threadId, () => load(true))
</script>

<template>
  <div class="media-panel">
    <div class="media-head">
      <Icon name="image" :size="16" />
      <span class="grow" style="font-weight: 600">媒体</span>
      <button class="btn sm ghost" :disabled="!!jobId" @click="prewarm">
        <Icon name="import" :size="13" /> 缓存缺失媒体
      </button>
      <button class="icon-btn" style="width: 26px; height: 26px" @click="emit('close')">
        <Icon name="close" :size="15" />
      </button>
    </div>

    <div class="media-tabs">
      <button
        v-for="[key, label] in TABS"
        :key="key"
        class="chip"
        :class="{ active: tab === key }"
        @click="switchTab(key)"
      >
        {{ label }}
        <span v-if="counts[key]" class="faint">{{ counts[key] }}</span>
      </button>
    </div>

    <div v-if="jobId" class="media-job">
      <JobPanel :job-id="jobId" title="缓存媒体" @done="onJobDone" />
    </div>

    <div class="media-body">
      <div v-if="loading && !items.length" class="hint" style="padding: 20px">读取中…</div>

      <div v-else-if="!items.length" class="hint" style="padding: 20px; text-align: center">
        这个会话还没有{{ tabLabel }}
      </div>

      <!-- 图片 / 表情 -->
      <div v-else-if="tab === 'image' || tab === 'sticker'" class="media-grid">
        <button
          v-for="item in items"
          :key="item.id"
          class="media-cell"
          @click="emit('open-image', imageSrc(item))"
        >
          <img
            v-if="!broken.has(item.id) && imageSrc(item)"
            :src="imageSrc(item)"
            loading="lazy"
            @error="onImageError(item.id)"
          />
          <span v-else class="missing">无法读取</span>
        </button>
      </div>

      <!-- 视频 -->
      <div v-else-if="tab === 'video'" class="video-list">
        <button v-for="item in items" :key="item.id" class="video-row" @click="openVideo(item)">
          <span class="play-dot"><Icon name="play" :size="14" /></span>
          <span class="grow">
            <span class="v-title">{{ item.text || kindLabel(item.kind) }}</span>
            <span class="faint small">{{ fmtTime(item.ts) }}</span>
          </span>
          <span v-if="item.duration" class="faint small">{{ Math.round(item.duration) }}″</span>
        </button>
      </div>

      <!-- 文件 -->
      <div v-else class="video-list">
        <div v-for="item in items" :key="item.id" class="video-row static">
          <Icon name="file" :size="16" />
          <span class="grow">
            <span class="v-title">{{ item.meta?.file?.name || item.text || '文件' }}</span>
            <span class="faint small">{{ fmtTime(item.ts) }}</span>
          </span>
          <span v-if="item.meta?.file?.size" class="faint small">
            {{ fmtBytes(item.meta.file.size) }}
          </span>
        </div>
      </div>

      <div v-if="hasMore" class="row" style="justify-content: center; padding: 10px">
        <button class="btn sm" :disabled="loading" @click="load(false)">
          <span v-if="loading" class="spinner" /> 加载更多
        </button>
      </div>
    </div>

    <!-- 视频播放浮层 -->
    <div v-if="playerUrl" class="player-mask" @click.self="playerUrl = ''">
      <div class="player-box">
        <video :src="playerUrl" controls autoplay @error="toast('这个视频取不到（缓存缺失或被清理）', 'err')" />
        <button class="btn sm ghost" @click="playerUrl = ''">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.media-panel {
  position: absolute;
  inset: 0;
  background: var(--bg);
  z-index: 20;
  display: flex;
  flex-direction: column;
}
.media-head { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.media-tabs { display: flex; gap: 6px; padding: 10px 14px; border-bottom: 1px solid var(--border-soft); }
.media-body { flex: 1; overflow: auto; padding: 12px 14px; }
.media-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 8px; }
.media-cell {
  aspect-ratio: 1;
  border: var(--border-w, 1px) solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--panel);
  cursor: pointer;
  padding: 0;
  display: grid;
  place-items: center;
}
.media-cell img { width: 100%; height: 100%; object-fit: cover; }
.media-cell .missing { font-size: 11px; color: var(--text-faint); }
.video-list { display: flex; flex-direction: column; gap: 6px; }
.video-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: var(--border-w, 1px) solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.video-row.static { cursor: default; }
.play-dot {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  display: grid;
  place-items: center;
}
.v-title { display: block; font-size: 13px; }
.media-job { padding: 10px 14px 0; }
.player-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.72);
  display: grid;
  place-items: center;
  z-index: 60;
}
.player-box { display: flex; flex-direction: column; gap: 10px; max-width: 80vw; }
.player-box video { max-width: 80vw; max-height: 78vh; border-radius: var(--radius); background: #000; }
</style>
