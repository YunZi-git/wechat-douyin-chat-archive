<script setup>
import { computed } from 'vue'
import { api } from '../lib/api'
import { store, applyTheme, toast } from '../lib/store'
import Icon from './Icon.vue'

const THEMES = ['dark', 'light', 'warm', 'purple']
const NEXT_LABEL = { dark: '浅色', light: '暖棕', warm: '紫夜', purple: '深色' }

const items = [
  { key: 'chats', icon: 'chat', label: '聊天' },
  { key: 'merge', icon: 'merge', label: '合并好友' },
  { key: 'import', icon: 'import', label: '导入数据' },
  { key: 'export', icon: 'export', label: '导出' },
  { key: 'settings', icon: 'settings', label: '设置' },
]

const mergedCount = computed(() => store.stats?.merged_contacts || 0)
const totalMessages = computed(() => store.stats?.messages || 0)

function go(key) {
  store.view = key
}

async function cycleTheme() {
  const idx = THEMES.indexOf(store.settings.theme)
  const next = THEMES[(idx + 1) % THEMES.length]
  applyTheme(next)
  try {
    await api.saveSettings({ theme: next })
  } catch {
    /* 存不上也不影响本次使用 */
  }
  toast(`主题：${NEXT_LABEL[store.settings.theme] || next}`)
}
</script>

<template>
  <aside class="rail">
    <div class="rail-logo" :title="`聊天信息工作台 · ${totalMessages.toLocaleString()} 条消息`">聊</div>

    <button
      v-for="item in items"
      :key="item.key"
      class="rail-btn"
      :class="{ active: store.view === item.key }"
      :title="item.label"
      @click="go(item.key)"
    >
      <Icon :name="item.icon" :size="20" />
      <span v-if="item.key === 'merge' && mergedCount" class="badge">{{ mergedCount }}</span>
    </button>

    <div class="rail-spacer" />

    <button class="rail-btn" :title="`切换主题（当前：${store.settings.theme}）`" @click="cycleTheme">
      <Icon :name="store.settings.theme === 'light' ? 'sun' : 'moon'" :size="19" />
    </button>
  </aside>
</template>
