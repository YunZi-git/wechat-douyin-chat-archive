<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../lib/api'

const props = defineProps({
  jobId: { type: String, default: '' },
  title: { type: String, default: '' },
})
const emit = defineEmits(['done'])

const job = ref(null)
const logEl = ref(null)
let timer = null

const STATUS_LABEL = {
  queued: '排队中',
  running: '进行中',
  ok: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

async function poll() {
  if (!props.jobId) return
  try {
    const data = await api.job(props.jobId)
    job.value = data
    if (['ok', 'failed', 'cancelled'].includes(data.status)) {
      clearInterval(timer)
      timer = null
      emit('done', data)
    }
    requestAnimationFrame(() => {
      if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
    })
  } catch {
    clearInterval(timer)
    timer = null
  }
}

function start() {
  clearInterval(timer)
  timer = setInterval(poll, 1000)
  poll()
}

onMounted(start)
onUnmounted(() => clearInterval(timer))
watch(() => props.jobId, () => {
  job.value = null
  if (props.jobId) start()
  else clearInterval(timer)
})

async function cancel() {
  if (props.jobId) await api.cancelJob(props.jobId)
}
</script>

<template>
  <div v-if="job" class="job-panel card" style="padding: 16px">
    <div class="row" style="margin-bottom: 10px">
      <span class="grow" style="font-weight: 600">{{ title || job.title }}</span>
      <span class="chip" :class="{ active: job.status === 'running' }">{{ STATUS_LABEL[job.status] || job.status }}</span>
      <button v-if="job.status === 'running'" class="btn sm ghost" @click="cancel">取消</button>
    </div>

    <div v-if="job.progress && job.progress.messages" class="hint" style="margin-bottom: 8px">
      已写入 {{ job.progress.messages.toLocaleString() }} 条消息
      <template v-if="job.progress.total"> / 共 {{ job.progress.total.toLocaleString() }}</template>
    </div>

    <div ref="logEl" class="log">{{ (job.log || []).join('\n') || '等待日志…' }}</div>

    <div v-if="job.status === 'failed'" class="err-box">
      {{ job.error }}
    </div>
  </div>
</template>

<style scoped>
.err-box {
  margin-top: 10px;
  padding: 9px 12px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--danger) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--danger) 36%, transparent);
  color: var(--danger);
  font-size: 12.5px;
}
</style>
