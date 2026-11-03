<script setup lang="ts">
// [回答反馈] 按轨迹ID保存单份评价，防止把整段会话或重复点击累计为多次评分。
import { ref } from 'vue'
import { request } from '../../shared/api/httpClient'
const props = defineProps<{ traceId: string }>()
const rating = ref('')
const busy = ref(false)
const error = ref('')
async function send(value: string) {
  // 请求期间阻止重叠提交；失败时保留未成功状态，允许用户稍后重试。
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await request(
      `/agent/observability/traces/${encodeURIComponent(props.traceId)}/feedback`,
      'PUT',
      { rating: value },
    )
    rating.value = value
  } catch {
    error.value = '反馈暂未保存，请稍后重试'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="section-actions" aria-label="回答反馈">
    <button
      class="ops-button"
      :aria-pressed="rating === 'helpful'"
      :disabled="busy"
      @click="send('helpful')"
    >
      {{ rating === 'helpful' ? '已反馈有帮助' : '有帮助' }}
    </button>
    <button
      class="ops-button"
      :aria-pressed="rating === 'unhelpful'"
      :disabled="busy"
      @click="send('unhelpful')"
    >
      {{ rating === 'unhelpful' ? '已反馈需改进' : '需改进' }}
    </button>
    <small v-if="error" role="status">{{ error }}</small>
  </div>
</template>
