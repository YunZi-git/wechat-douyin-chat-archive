<script setup>
import { computed } from 'vue'
import { api } from '../lib/api'
import { store, currentTheme } from '../lib/store'
import Icon from './Icon.vue'

const items = [
  { key: 'chats', icon: 'chat', label: '聊天' },
  { key: 'voice', icon: 'mic', label: '语音识别' },
  { key: 'merge', icon: 'merge', label: '合并好友' },
  { key: 'import', icon: 'import', label: '导入数据' },
  { key: 'export', icon: 'export', label: '导出' },
  { key: 'appearance', icon: 'sparkles', label: '外观' },
  { key: 'settings', icon: 'settings', label: '设置' },
]

const mergedCount = computed(() => store.stats?.merged_contacts || 0)
const totalMessages = computed(() => store.stats?.messages || 0)
const theme = computed(() => currentTheme())

function go(key) {
  store.view = key
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

    <button
      class="rail-btn"
      :title="`外观：${theme.name}`"
      @click="store.view = 'appearance'"
    >
      <Icon :name="theme.dark ? 'moon' : 'sun'" :size="19" />
    </button>
  </aside>
</template>
