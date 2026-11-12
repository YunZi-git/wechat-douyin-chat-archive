<script setup>
import { computed, ref } from 'vue'
import { fmtCount, fmtDateTime, initial, avatarColor, PLATFORM_META } from '../lib/format'
import Icon from './Icon.vue'

const props = defineProps({
  thread: { type: Object, default: null },
})
const emit = defineEmits(['close', 'rename', 'unmerge', 'export'])

const editing = ref(false)
const draft = ref('')
const mediaUrl = (p) => (p ? `/media/${p}` : '')

const isContact = computed(() => props.thread?.thread_kind === 'contact')
const contact = computed(() => props.thread?.contact || {})
const conversation = computed(() => props.thread?.conversation || {})
const title = computed(() => (isContact.value ? contact.value.display_name : conversation.value.title))
const avatar = computed(() => (isContact.value ? contact.value.avatar_url : conversation.value.avatar_url))
const identities = computed(() => props.thread?.identities || [])
const conversations = computed(() => props.thread?.conversations || [])
const members = computed(() => props.thread?.members || [])
const platforms = computed(() => props.thread?.platforms || [])

const totalMessages = computed(() =>
  conversations.value.reduce((sum, c) => sum + (c.msg_count || 0), 0)
)

function startEdit() {
  draft.value = title.value || ''
  editing.value = true
}
function commit() {
  const name = draft.value.trim()
  editing.value = false
  if (name && name !== title.value) emit('rename', name)
}

function shortUid(uid) {
  const s = String(uid || '')
  return s.length > 18 ? `${s.slice(0, 8)}…${s.slice(-6)}` : s
}
</script>

<template>
  <aside v-if="thread" class="info">
    <div class="info-head">
      <div class="avatar" :style="{ background: avatarColor(thread.thread_id) + '24', color: avatarColor(thread.thread_id) }">
        <img v-if="avatar" :src="mediaUrl(avatar)" alt="" />
        <template v-else>{{ initial(title) }}</template>
      </div>

      <template v-if="!editing">
        <h3>{{ title }}</h3>
        <div class="row" style="justify-content: center; gap: 6px; margin-top: 6px">
          <span v-for="p in platforms" :key="p" class="plat-dot" :class="PLATFORM_META[p]?.cls">
            {{ PLATFORM_META[p]?.label }}
          </span>
          <span v-if="platforms.length > 1" class="group-tag">已合并</span>
        </div>
        <div v-if="isContact" class="mt12">
          <button class="btn sm ghost" @click="startEdit"><Icon name="settings" :size="12" /> 改个名字</button>
        </div>
      </template>
      <template v-else>
        <input v-model="draft" class="input" style="margin-top: 8px" @keyup.enter="commit" />
        <div class="row" style="justify-content: center; gap: 6px; margin-top: 8px">
          <button class="btn sm" @click="editing = false">取消</button>
          <button class="btn sm primary" @click="commit">保存</button>
        </div>
      </template>
    </div>

    <div class="info-block">
      <h4>概览</h4>
      <div class="kv"><span>消息总数</span><span>{{ fmtCount(totalMessages) }}</span></div>
      <div class="kv"><span>平台</span><span>{{ platforms.map((p) => PLATFORM_META[p]?.label).join(' + ') }}</span></div>
      <div class="kv" v-if="thread.thread_kind === 'conv' && conversation.member_count">
        <span>群成员</span><span>{{ conversation.member_count }}</span>
      </div>
      <div class="kv" v-if="isContact"><span>会话数</span><span>{{ conversations.length }}</span></div>
    </div>

    <div v-if="identities.length" class="info-block">
      <h4>平台账号</h4>
      <div v-for="i in identities" :key="i.id" class="identity">
        <span class="plat-dot" :class="PLATFORM_META[i.platform]?.cls">{{ PLATFORM_META[i.platform]?.short }}</span>
        <span class="name">{{ i.platform_name || '未命名' }}</span>
        <span class="uid" :title="i.platform_uid">{{ shortUid(i.platform_uid) }}</span>
      </div>
      <div v-if="platforms.length > 1" class="row wrap" style="gap: 6px; margin-top: 8px">
        <button
          v-for="p in platforms"
          :key="p"
          class="btn sm ghost danger"
          @click="emit('unmerge', p)"
        >解绑{{ PLATFORM_META[p]?.label }}</button>
      </div>
    </div>

    <div v-if="conversations.length" class="info-block">
      <h4>来源会话</h4>
      <div v-for="c in conversations" :key="c.id" class="identity">
        <span class="plat-dot" :class="PLATFORM_META[c.platform]?.cls">{{ PLATFORM_META[c.platform]?.short }}</span>
        <span class="name">{{ c.kind === 'group' ? '群聊' : c.title || '私聊' }}</span>
        <span class="uid">{{ fmtCount(c.msg_count) }}</span>
      </div>
    </div>

    <div v-if="members.length" class="info-block">
      <h4>活跃成员</h4>
      <div v-for="(m, idx) in members.slice(0, 10)" :key="m.uid" class="identity">
        <span class="uid" style="width: 16px">{{ idx + 1 }}</span>
        <span class="name">{{ m.name || m.uid }}</span>
        <span class="uid">{{ fmtCount(m.n) }}</span>
      </div>
    </div>

    <div class="info-block" style="border-bottom: 0">
      <button class="btn block" @click="emit('export')"><Icon name="export" :size="14" /> 导出这个会话</button>
      <button class="btn block ghost mt12" @click="emit('close')"><Icon name="close" :size="14" /> 收起信息栏</button>
    </div>
  </aside>
</template>

<style scoped>
.avatar img { width: 100%; height: 100%; object-fit: cover; }
</style>
