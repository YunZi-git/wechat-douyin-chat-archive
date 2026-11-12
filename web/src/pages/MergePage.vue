<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { store, toast, refreshStats, openThread } from '../lib/store'
import { fmtCount, fmtRelative, initial, avatarColor, PLATFORM_META } from '../lib/format'
import Icon from '../components/Icon.vue'

const contacts = ref([])
const loading = ref(true)
const tab = ref('single')     // single | merged | all
const query = ref('')
const selected = ref([])
const mergedName = ref('')
const busy = ref(false)

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return contacts.value.filter((c) => {
    if (tab.value === 'single' && c.merged) return false
    if (tab.value === 'merged' && !c.merged) return false
    if (!q) return true
    return (
      (c.display_name || '').toLowerCase().includes(q) ||
      (c.platform_names || '').toLowerCase().includes(q)
    )
  })
})

const canMerge = computed(() => selected.value.length === 2)

async function load() {
  loading.value = true
  try {
    const res = await api.contacts()
    contacts.value = res.items
  } catch (err) {
    toast(`读取联系人失败：${err.message}`, 'err')
  } finally {
    loading.value = false
  }
}

function toggle(c) {
  const idx = selected.value.findIndex((x) => x.id === c.id)
  if (idx >= 0) {
    selected.value.splice(idx, 1)
    return
  }
  if (selected.value.length >= 2) {
    toast('一次只能合并两个联系人', 'err')
    return
  }
  selected.value.push(c)
  if (selected.value.length === 2) {
    mergedName.value = selected.value[0].display_name || ''
  }
}

function isSelected(c) {
  return selected.value.some((x) => x.id === c.id)
}

async function doMerge() {
  if (!canMerge.value) return
  busy.value = true
  try {
    const [a, b] = selected.value
    await api.merge(a.id, b.id, mergedName.value.trim() || a.display_name)
    toast('合并完成', 'ok')
    selected.value = []
    await load()
    await refreshStats()
  } catch (err) {
    toast(`合并失败：${err.message}`, 'err')
  } finally {
    busy.value = false
  }
}

async function unmerge(c, platform) {
  busy.value = true
  try {
    await api.unmerge(c.id, platform)
    toast(`已解绑${PLATFORM_META[platform]?.label || platform}`, 'ok')
    await load()
    await refreshStats()
  } catch (err) {
    toast(`解绑失败：${err.message}`, 'err')
  } finally {
    busy.value = false
  }
}

function chatWith(c) {
  openThread(`contact:${c.id}`)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="row">
        <div class="grow">
          <h1>合并好友</h1>
          <p>
            同一个人可能既有微信好友也有抖音好友。选中两条记录，把它们合并成一个人，
            之后两个平台的聊天会交错在同一条时间线上。合并只影响本机数据，可以随时解绑。
          </p>
        </div>
        <button class="btn" @click="load"><Icon name="refresh" :size="14" /> 刷新</button>
      </div>
    </div>

    <div class="row wrap" style="gap: 10px; margin-bottom: 16px">
      <div class="chip-row">
        <button class="chip" :class="{ active: tab === 'single' }" @click="tab = 'single'">
          未合并 {{ contacts.filter((c) => !c.merged).length }}
        </button>
        <button class="chip" :class="{ active: tab === 'merged' }" @click="tab = 'merged'">
          已合并 {{ contacts.filter((c) => c.merged).length }}
        </button>
        <button class="chip" :class="{ active: tab === 'all' }" @click="tab = 'all'">
          全部 {{ contacts.length }}
        </button>
      </div>
      <div class="search-box grow" style="max-width: 320px; margin-left: auto">
        <Icon name="search" :size="14" />
        <input v-model="query" placeholder="搜名字 / 平台昵称…" />
      </div>
    </div>

    <div v-if="loading" class="row"><div class="spinner" /> <span class="hint">读取中…</span></div>

    <div v-else class="merge-list">
      <div
        v-for="c in filtered"
        :key="c.id"
        class="row-card"
        :class="{ selected: isSelected(c) }"
      >
        <div class="checkbox" :class="{ on: isSelected(c) }" @click="toggle(c)">
          <Icon name="check" :size="12" />
        </div>

        <div
          class="avatar"
          style="width: 38px; height: 38px; border-radius: 11px"
          :style="{ background: avatarColor(c.id) + '24', color: avatarColor(c.id) }"
        >
          {{ initial(c.display_name) }}
        </div>

        <div class="row-main" @click="toggle(c)">
          <div class="row-title">
            <span class="clickable">{{ c.display_name }}</span>
            <span v-for="p in c.platforms" :key="p" class="plat-dot" :class="PLATFORM_META[p]?.cls">
              {{ PLATFORM_META[p]?.label }}
            </span>
            <span v-if="c.merged" class="group-tag">已合并</span>
          </div>
          <div class="row-sub">
            {{ fmtCount(c.msg_count) }} 条消息 · 最近 {{ fmtRelative(c.last_ts) }}
            <template v-if="c.platform_names"> · {{ c.platform_names }}</template>
          </div>
        </div>

        <div class="row-actions">
          <button class="btn sm ghost" title="查看会话" @click="chatWith(c)"><Icon name="chat" :size="13" /></button>
          <template v-if="c.merged">
            <button
              v-for="p in c.platforms"
              :key="p"
              class="btn sm ghost danger"
              @click="unmerge(c, p)"
            >解绑{{ PLATFORM_META[p]?.short }}</button>
          </template>
        </div>
      </div>

      <div v-if="!filtered.length" class="empty" style="height: 240px">
        <div>
          <div class="icon"><Icon name="users" :size="22" /></div>
          <h3>没有符合条件的联系人</h3>
          <p>试着切换到「全部」，或者清空搜索词。</p>
        </div>
      </div>
    </div>

    <!-- 选中两条后浮出的合并操作条 -->
    <div v-if="selected.length" class="merge-bar">
      <div class="row wrap grow" style="gap: 10px">
        <span class="hint">已选：</span>
        <span v-for="(c, i) in selected" :key="c.id" class="pill">
          <span
            v-for="p in c.platforms"
            :key="p"
            class="plat-dot"
            :class="PLATFORM_META[p]?.cls"
          >{{ PLATFORM_META[p]?.short }}</span>
          {{ c.display_name }}
          <button class="x" @click="toggle(c)"><Icon name="close" :size="11" /></button>
        </span>
        <span v-if="selected.length === 1" class="hint">再选一个 →</span>
      </div>

      <template v-if="canMerge">
        <input v-model="mergedName" class="input" style="max-width: 210px" placeholder="合并后的名字" />
        <button class="btn primary" :disabled="busy" @click="doMerge">
          <span v-if="busy" class="spinner" />
          <Icon v-else name="merge" :size="14" />
          合并这两个
        </button>
      </template>
      <button class="btn ghost" @click="selected = []">清空</button>
    </div>
  </div>
</template>

<style scoped>
.chip-row { display: flex; gap: 6px; }
.merge-list { display: flex; flex-direction: column; gap: 8px; padding-bottom: 90px; }
.merge-bar {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  bottom: 22px;
  width: min(880px, calc(100% - 160px));
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  z-index: 20;
  backdrop-filter: blur(8px);
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--panel-3);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12.5px;
}
.pill .x {
  border: 0;
  background: transparent;
  color: var(--text-faint);
  display: grid;
  place-items: center;
  padding: 0;
}
.clickable { cursor: pointer; }
</style>
