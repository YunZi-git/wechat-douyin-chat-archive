<script setup>
import { onMounted, ref, watch, computed, nextTick } from 'vue'
import { api } from '../lib/api'
import { store, toast, refreshStats, openThread } from '../lib/store'
import ThreadList from '../components/ThreadList.vue'
import ChatView from '../components/ChatView.vue'
import ThreadInfo from '../components/ThreadInfo.vue'
import ExportDialog from '../components/ExportDialog.vue'
import Icon from '../components/Icon.vue'

const threads = ref([])
const total = ref(0)
const loadingThreads = ref(false)
const platform = ref('')
const accounts = ref([])
const account = ref('')
const query = ref('')
const mergedOnly = ref(false)

const thread = ref(null)
const messages = ref([])
const hasMoreBefore = ref(false)
const loadingMessages = ref(false)
const messagePlatform = ref('')

const infoOpen = ref(true)
const exportOpen = ref(false)
const lightbox = ref('')

let queryTimer = null
let loadToken = 0

const identity = ref(null)
const identityHint = computed(() => {
  const info = identity.value
  if (!info) return null
  if (info.needs_setup) return '还没有确认聊天里的「我」是谁，左右分栏可能不对'
  if (info.needs_confirm) return '抖音的「我」是自动推断的，如果左右分栏不对请改一下'
  return null
})

async function loadIdentity() {
  try {
    identity.value = await api.selfIdentity()
  } catch {
    identity.value = null
  }
}

const hasData = computed(() => (store.stats?.messages || 0) > 0)
const pageSize = computed(() => store.settings.page_size || 60)

async function loadThreads() {
  loadingThreads.value = true
  try {
    const res = await api.threads({
      platform: platform.value || undefined,
      account: account.value || undefined,
      q: query.value || undefined,
      limit: 400,
    })
    threads.value = res.items
    total.value = res.total
    if (!store.activeThreadId && res.items.length) {
      selectThread(res.items[0])
    }
  } catch (err) {
    toast(`读取会话失败：${err.message}`, 'err')
  } finally {
    loadingThreads.value = false
  }
}

async function selectThread(item) {
  store.activeThreadId = item.thread_id
  await loadThread(item.thread_id)
}

async function loadThread(id) {
  if (!id) return
  const token = ++loadToken
  try {
    const info = await api.thread(id)
    if (token !== loadToken) return
    thread.value = info
    messagePlatform.value = ''
    await loadMessages(id)
  } catch (err) {
    toast(`读取会话失败：${err.message}`, 'err')
  }
}

async function loadMessages(id) {
  loadingMessages.value = true
  try {
    const res = await api.messages(id, {
      limit: pageSize.value,
      platform: messagePlatform.value || undefined,
    })
    messages.value = res.items
    hasMoreBefore.value = res.has_more_before
  } catch (err) {
    toast(`读取消息失败：${err.message}`, 'err')
  } finally {
    loadingMessages.value = false
  }
}

async function loadOlder() {
  if (!thread.value || loadingMessages.value || !hasMoreBefore.value) return
  const oldest = messages.value[0]?.ts
  if (!oldest) return
  loadingMessages.value = true
  try {
    const res = await api.messages(thread.value.thread_id, {
      limit: pageSize.value,
      beforeTs: oldest,
      platform: messagePlatform.value || undefined,
    })
    messages.value = [...res.items, ...messages.value]
    hasMoreBefore.value = res.has_more_before
  } catch (err) {
    toast(`加载更早消息失败：${err.message}`, 'err')
  } finally {
    loadingMessages.value = false
  }
}

async function jumpTo({ ts, msgId }) {
  if (!thread.value) return
  loadingMessages.value = true
  try {
    // 一条请求拿到该时间点前后各一半，且一定包含目标本身
    const res = await api.messages(thread.value.thread_id, {
      limit: 80,
      aroundTs: ts,
      platform: messagePlatform.value || undefined,
    })
    messages.value = res.items
    hasMoreBefore.value = res.has_more_before
    if (msgId) {
      await nextTick()
      document
        .querySelector(`[data-msg-id="${msgId}"]`)
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  } catch (err) {
    toast(`跳转失败：${err.message}`, 'err')
  } finally {
    loadingMessages.value = false
  }
}

async function onPlatformFilter(p) {
  messagePlatform.value = p
  if (thread.value) await loadMessages(thread.value.thread_id)
}

async function rename(name) {
  if (!thread.value?.contact) return
  try {
    await api.rename(thread.value.contact.id, name)
    await loadThread(thread.value.thread_id)
    await loadThreads()
    toast('已改名', 'ok')
  } catch (err) {
    toast(`改名失败：${err.message}`, 'err')
  }
}

async function unmerge(p) {
  if (!thread.value?.contact) return
  try {
    await api.unmerge(thread.value.contact.id, p)
    await loadThread(thread.value.thread_id)
    await loadThreads()
    await refreshStats()
    toast('已解绑', 'ok')
  } catch (err) {
    toast(`解绑失败：${err.message}`, 'err')
  }
}

function onQuery(value) {
  query.value = value
  clearTimeout(queryTimer)
  queryTimer = setTimeout(loadThreads, 260)
}

watch(platform, loadThreads)
watch(account, loadThreads)

async function loadAccounts() {
  try {
    const res = await api.accounts()
    accounts.value = res.items || []
  } catch {
    accounts.value = []
  }
}

// 在设置里改了每页条数，回到聊天页时按新值重新取
watch(
  () => store.settings.page_size,
  () => {
    if (thread.value) loadMessages(thread.value.thread_id)
  }
)
watch(
  () => store.activeThreadId,
  (id) => {
    if (id && thread.value?.thread_id !== id) loadThread(id)
  }
)

watch(
  () => store.focusSeq,
  () => {
    if (store.activeThreadId) loadThread(store.activeThreadId)
  }
)

// 从搜索页/日期跳转过来：先切会话，再定位到具体时间点
watch(
  () => store.dataVersion,
  () => {
    // 后台增量更新完成后刷新会话列表（不动当前阅读位置）
    loadThreads()
    loadIdentity()
  }
)

watch(
  () => store.pendingJump?.seq,
  async () => {
    const jump = store.pendingJump
    if (!jump) return
    store.pendingJump = null
    if (thread.value?.thread_id !== jump.threadId) {
      await loadThread(jump.threadId)
    }
    if (jump.ts) await jumpTo({ ts: jump.ts, msgId: jump.msgId })
  }
)

onMounted(async () => {
  loadAccounts()
  // 支持 ?thread=contact:12 深链，方便从合并页/导出直接定位
  const deep = new URLSearchParams(location.search).get('thread')
  if (deep) {
    store.activeThreadId = deep
    await loadThread(deep)
  }
  await loadThreads()
  await loadIdentity()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="identityHint" class="identity-tip">
      <Icon name="users" :size="16" />
      <span>{{ identityHint }}</span>
      <button class="btn sm primary" @click="store.view = 'settings'">去设置</button>
    </div>
  </Teleport>

  <!-- 一条数据都没有：直接把用户引到导入页 -->
  <div v-if="!hasData" class="empty" style="height: 100%">
    <div>
      <div class="icon"><Icon name="database" :size="26" /></div>
      <h3>还没有导入任何聊天记录</h3>
      <p style="margin-bottom: 18px">
        支持导入微信（已解密数据库）和抖音（chat.db）。导入后可以把同一个人的两个平台账号合并，按时间交错浏览。
      </p>
      <button class="btn primary" @click="store.view = 'import'">
        <Icon name="import" :size="15" /> 去导入数据
      </button>
    </div>
  </div>

  <div v-else class="chats-layout" :class="{ 'with-info': infoOpen && thread }">
    <ThreadList
      :accounts="accounts"
      :account="account"
      @update:account="account = $event"
      :threads="threads"
      :total="total"
      :loading="loadingThreads"
      :platform="platform"
      :query="query"
      :active-id="store.activeThreadId || ''"
      :merged-only="mergedOnly"
      @select="selectThread"
      @update:platform="(v) => (platform = v)"
      @update:query="onQuery"
      @update:merged-only="(v) => (mergedOnly = v)"
      @refresh="loadThreads"
    />

    <ChatView
      :thread="thread"
      :messages="messages"
      :has-more-before="hasMoreBefore"
      :platform="messagePlatform"
      :loading="loadingMessages"
      @load-older="loadOlder"
      @toggle-info="infoOpen = !infoOpen"
      @update:platform="onPlatformFilter"
      @export="exportOpen = true"
      @open-image="lightbox = $event"
      @jump-to="jumpTo"
    />

    <ThreadInfo
      @open-thread="openThread"
      v-if="infoOpen && thread"
      :thread="thread"
      @close="infoOpen = false"
      @rename="rename"
      @unmerge="unmerge"
      @export="exportOpen = true"
    />

    <ExportDialog
      v-if="exportOpen && thread"
      :thread-id="thread.thread_id"
      :title="thread.thread_kind === 'contact' ? thread.contact?.display_name : thread.conversation?.title"
      :platforms="thread.platforms || []"
      @close="exportOpen = false"
    />

    <div v-if="lightbox" class="modal-mask" @click="lightbox = ''">
      <img :src="lightbox" class="lightbox-img" alt="" />
    </div>
  </div>
</template>

<style scoped>
.lightbox-img {
  max-width: 92vw;
  max-height: 90vh;
  border-radius: 10px;
  box-shadow: var(--shadow);
}
.identity-tip {
  position: fixed;
  left: 18px;
  bottom: 18px;
  z-index: 60;
  display: flex;
  align-items: center;
  gap: 9px;
  max-width: 520px;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel);
  box-shadow: var(--shadow-lg, 0 8px 30px rgba(0, 0, 0, 0.18));
  font-size: 12.5px;
  color: var(--text-dim);
}
</style>
