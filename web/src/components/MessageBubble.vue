<script setup>
import { computed } from 'vue'
import { fmtTime, initial, avatarColor, PLATFORM_META, kindLabel } from '../lib/format'
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
const imageSrc = computed(() => m.value.media_url || '')
const isVoice = computed(() => m.value.kind === 'voice')
const isVideo = computed(() => m.value.kind === 'video')
const voiceText = computed(() => (m.value.transcript || '').trim())
const audioSrc = computed(() => `/api/messages/${m.value.id}/audio`)
const hasAudio = computed(() => !!m.value.media_url)
</script>

<template>
  <!-- 系统消息：居中的小字，不带头像 -->
  <div v-if="isSystem" class="msg-row system">
    <div class="msg-body">
      <div class="bubble system">{{ m.text || '系统消息' }}</div>
    </div>
  </div>

  <div v-else class="msg-row" :class="{ self: m.is_self }">
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

        <!-- 图片 / 表情 -->
        <template v-if="isImage">
          <img
            v-if="imageSrc"
            class="media"
            :class="{ sticker: m.kind === 'sticker' }"
            :src="imageSrc"
            loading="lazy"
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
          <video v-if="m.media_url" :src="m.media_url" controls preload="none" />
          <span v-else class="faint">{{ m.text || '[视频]' }}</span>
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
</style>
