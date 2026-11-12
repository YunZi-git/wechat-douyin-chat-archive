<script setup>
import { computed, ref } from 'vue'
import { fmtTime, fmtBytes, initial, avatarColor, PLATFORM_META, kindLabel } from '../lib/format'
import Icon from './Icon.vue'

const props = defineProps({
  message: { type: Object, required: true },
  showSender: { type: Boolean, default: false },
})
const emit = defineEmits(['open-image'])

const m = computed(() => props.message)
const isSystem = computed(() => ['system', 'revoke', 'call'].includes(m.value.kind))
const plat = computed(() => PLATFORM_META[m.value.platform] || { label: m.value.platform, cls: '' })
const color = computed(() => avatarColor(m.value.sender_uid || m.value.sender_name || 'self'))

const isImage = computed(() => ['image', 'sticker'].includes(m.value.kind))
const imgBroken = ref(false)
const videoBroken = ref(false)
const videoRequested = ref(!!props.message.media_url)

// 微信图片/表情的媒体文件是导入后才按需解密的，走 /image 接口
const imageSrc = computed(() => {
  if (m.value.media_url) return m.value.media_url
  if (m.value.platform === 'wechat' && ['image', 'sticker'].includes(m.value.kind)) {
    return `/api/messages/${m.value.id}/image`
  }
  return ''
})
const videoSrc = computed(() => {
  if (m.value.media_url) return m.value.media_url
  if (m.value.platform === 'wechat' && m.value.kind === 'video') {
    return `/api/messages/${m.value.id}/video`
  }
  return ''
})
const isVoice = computed(() => m.value.kind === 'voice')
const isVideo = computed(() => m.value.kind === 'video')
const voiceText = computed(() => (m.value.transcript || '').trim())
const audioSrc = computed(() => `/api/messages/${m.value.id}/audio`)
const hasAudio = computed(() => !!m.value.media_url)
const meta = computed(() => m.value.meta || {})
const quote = computed(() => meta.value.quote || null)
const fileMeta = computed(() => meta.value.file || null)
const forwardMeta = computed(() => meta.value.forward || null)
const locMeta = computed(() => meta.value.location || null)
const cardMeta = computed(() => meta.value.card || null)
const miniMeta = computed(() => meta.value.miniprogram || null)
</script>

<template>
  <!-- 系统消息：居中的小字，不带头像 -->
  <div v-if="isSystem" class="msg-row system" :data-msg-id="m.id">
    <div class="msg-body">
      <div class="bubble system">{{ m.text || '系统消息' }}</div>
    </div>
  </div>

  <div v-else class="msg-row" :data-msg-id="m.id" :class="{ self: m.is_self }">
    <div
      class="avatar"
      :style="{ background: m.is_self ? 'var(--panel-3)' : color + '22', color: m.is_self ? 'var(--text-dim)' : color }"
    >
      {{ m.is_self ? '我' : initial(m.sender_name) }}
    </div>

    <div class="msg-body">
      <div class="msg-top">
        <span v-if="showSender && !m.is_self" class="msg-sender">{{ m.sender_name || '未知' }}</span>
        <span class="plat-dot" :class="plat.cls">{{ plat.label }}</span>
        <span class="msg-time">{{ fmtTime(m.ts) }}</span>
      </div>

      <div class="bubble" :class="[`plat-${m.platform}`]">
        <div v-if="m.reply_to" class="quote">
          <Icon name="link" :size="12" />
          <span>{{ m.reply_to }}</span>
        </div>
        <div v-else-if="quote" class="quote">
          <Icon name="link" :size="12" />
          <span class="q-sender">{{ quote.sender || '引用' }}：</span>
          <span>{{ quote.content || '（原消息不可见）' }}</span>
        </div>

        <!-- 图片 / 表情 -->
        <template v-if="isImage">
          <img
            v-if="imageSrc && !imgBroken"
            class="media"
            :class="{ sticker: m.kind === 'sticker' }"
            :src="imageSrc"
            loading="lazy"
            @error="imgBroken = true"
            @click="emit('open-image', imageSrc)"
          />
          <span v-else class="faint">{{ m.text || kindLabel }}</span>
        </template>

        <!-- 语音：SILK 会在后端按需转 WAV -->
        <template v-else-if="isVoice">
          <div class="voice-line">
            <Icon name="mic" :size="15" />
            <audio v-if="hasAudio" :src="audioSrc" controls preload="metadata" />
            <span v-else class="faint">{{ m.transcript ? '语音文件缺失' : '语音未落地' }}</span>
            <span v-if="m.duration" class="voice-dur">{{ m.duration }}″</span>
          </div>
          <div v-if="voiceText" class="voice-text">{{ voiceText }}</div>
        </template>

        <!-- 视频 -->
        <template v-else-if="isVideo">
          <video
            v-if="videoSrc && videoRequested && !videoBroken"
            :src="videoSrc"
            controls
            autoplay
            preload="metadata"
            @error="videoBroken = true"
          />
          <button v-else-if="videoSrc && !videoBroken" class="btn sm ghost" @click="videoRequested = true">
            <Icon name="play" :size="13" /> 加载视频
          </button>
          <span v-else class="faint">{{ m.text || '[视频]' }}</span>
        </template>

        <!-- 文件 -->
        <template v-else-if="m.kind === 'file'">
          <span class="file-line">
            <Icon name="file" :size="15" />
            <span class="grow file-name">{{ fileMeta?.name || m.text }}</span>
            <span v-if="fileMeta?.size" class="faint small">{{ fmtBytes(fileMeta.size) }}</span>
          </span>
        </template>

        <!-- 合并转发 -->
        <template v-else-if="m.kind === 'forward'">
          <div class="forward-card">
            <div class="f-title"><Icon name="folder" :size="14" /> {{ forwardMeta?.title || '合并转发' }}</div>
            <div class="f-desc">{{ forwardMeta?.desc || m.text }}</div>
            <div class="f-count">{{ forwardMeta?.count || 0 }} 条消息</div>
          </div>
        </template>

        <!-- 位置 -->
        <template v-else-if="m.kind === 'location'">
          <span class="file-line">
            <Icon name="link" :size="14" />
            <span class="grow">{{ locMeta?.label || m.text }}</span>
            <span v-if="locMeta?.lat" class="faint small">{{ locMeta.lat }}, {{ locMeta.lng }}</span>
          </span>
        </template>

        <!-- 名片 -->
        <template v-else-if="m.kind === 'card'">
          <span class="file-line">
            <Icon name="users" :size="14" />
            <span class="grow">{{ cardMeta?.nickname || m.text }}</span>
            <span v-if="cardMeta?.alias" class="faint small">@{{ cardMeta.alias }}</span>
          </span>
        </template>

        <!-- 小程序 / 视频号 -->
        <template v-else-if="m.kind === 'miniprogram' || m.kind === 'channel'">
          <span class="share-line">
            <Icon name="link" :size="13" />
            {{ m.text }}
          </span>
          <span v-if="miniMeta?.source" class="faint small"> · {{ miniMeta.source }}</span>
        </template>

        <!-- 文本 / 分享 / 其他 -->
        <template v-else>
          <span v-if="m.kind === 'share'" class="share-line">
            <Icon name="link" :size="13" />
            {{ m.text || '[分享]' }}
          </span>
          <span v-else>{{ m.text || '（空消息）' }}</span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.quote {
  display: flex;
  gap: 5px;
  align-items: flex-start;
  font-size: 11.5px;
  color: var(--text-faint);
  border-left: 2px solid var(--border);
  padding-left: 7px;
  margin-bottom: 6px;
  line-height: 1.45;
  word-break: break-word;
}
.voice-text {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--text-dim);
  border-top: 1px dashed var(--border);
  padding-top: 6px;
}
.share-line { display: inline-flex; gap: 6px; align-items: center; color: var(--accent); }
.sticker {
  background: transparent;
  border: 0;
  max-width: 120px;
  max-height: 120px;
}
.q-sender { color: var(--text-dim); }
.file-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 180px;
  max-width: 340px;
}
.file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.forward-card { min-width: 200px; }
.f-title { display: flex; gap: 6px; align-items: center; font-weight: 600; }
.f-desc {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-dim);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.f-count { margin-top: 6px; font-size: 11px; color: var(--text-faint); border-top: 1px dashed var(--border); padding-top: 5px; }
</style>
