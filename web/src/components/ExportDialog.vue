<script setup>
import { computed, ref } from 'vue'
import { api, downloadUrl } from '../lib/api'
import { fmtBytes, PLATFORM_META } from '../lib/format'
import { toast } from '../lib/store'
import Icon from './Icon.vue'

const props = defineProps({
  threadId: { type: String, required: true },
  title: { type: String, default: '' },
  platforms: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

const format = ref('jsonl')
const platform = ref('')
const busy = ref(false)
const result = ref(null)

const formats = [
  { key: 'jsonl', label: 'ChatLab JSONL', hint: '逐行 JSON，适合大文件和分析工具' },
  { key: 'json', label: 'ChatLab JSON', hint: '完整 JSON，便于人工查看' },
  { key: 'markdown', label: 'Markdown', hint: '纯文本归档，可直接阅读' },
]

async function run() {
  busy.value = true
  result.value = null
  try {
    const res = await api.exportThread(props.threadId, format.value, platform.value || undefined)
    result.value = res
    toast('导出完成', 'ok')
  } catch (err) {
    toast(`导出失败：${err.message}`, 'err')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal">
      <h3>导出会话</h3>
      <p class="sub">{{ title }}</p>

      <div class="field">
        <label>格式</label>
        <div class="rows">
          <button
            v-for="f in formats"
            :key="f.key"
            class="row-card"
            :class="{ selected: format === f.key }"
            @click="format = f.key"
          >
            <div class="checkbox" :class="{ on: format === f.key }"><Icon name="check" :size="12" /></div>
            <div class="row-main">
              <div class="row-title">{{ f.label }}</div>
              <div class="row-sub">{{ f.hint }}</div>
            </div>
          </button>
        </div>
      </div>

      <div v-if="platforms.length > 1" class="field">
        <label>范围</label>
        <select v-model="platform" class="select">
          <option value="">合并导出（微信 + 抖音）</option>
          <option v-for="p in platforms" :key="p" :value="p">只导出 {{ PLATFORM_META[p]?.label || p }}</option>
        </select>
      </div>

      <div v-if="result" class="result-box">
        <div class="row">
          <Icon name="file" :size="15" />
          <span class="grow nowrap" style="overflow: hidden; text-overflow: ellipsis">{{ result.name }}</span>
          <span class="faint small">{{ fmtBytes(result.size) }}</span>
        </div>
        <a class="btn sm primary mt12" :href="downloadUrl(result.name)" download>
          <Icon name="import" :size="13" /> 下载文件
        </a>
      </div>

      <div class="modal-actions">
        <button class="btn" @click="emit('close')">关闭</button>
        <button class="btn primary" :disabled="busy" @click="run">
          <span v-if="busy" class="spinner" />
          <Icon v-else name="export" :size="14" />
          {{ busy ? '导出中…' : '开始导出' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-box {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
  margin-top: 6px;
}
</style>
