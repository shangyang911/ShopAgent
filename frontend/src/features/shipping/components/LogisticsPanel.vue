<script setup lang="ts">
// 只展示平台保存的事实；失败保留上次轨迹，不把订单完成或高德耗时当成签收/快递时效。
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchTracking, syncTracking } from '../api/trackingApi'
import type { TrackingSnapshot } from '../model/tracking'
import { dateTime } from '../../../shared/commerce'

const props = withDefaults(defineProps<{ orderId: number; packageNo?: number }>(), { packageNo: 1 })
// 页面切换订单时由父组件重新挂载面板；面板只管理本单的读取、同步与提示状态。
const emit = defineEmits<{ updated: [] }>()
const snapshot = ref<TrackingSnapshot | null>(null)
const loading = ref(false)
const syncing = ref(false)
const error = ref('')
const notice = ref('')
let disposed = false
// 请求序号用于丢弃旧响应；disposed 用于阻止卸载后的异步结果继续修改页面。
let generation = 0
async function load() {
  // 读取的是已保存快照，不触发快递平台查询；失败时不清空上一份可用数据。
  const id = ++generation
  loading.value = true
  error.value = ''
  try {
    const result = await fetchTracking(props.orderId, props.packageNo)
    if (!disposed && id === generation) snapshot.value = result
  } catch (reason) {
    if (!disposed && id === generation)
      error.value = reason instanceof Error ? reason.message : '物流记录读取失败'
  } finally {
    if (!disposed && id === generation) loading.value = false
  }
}
async function synchronize() {
  // 前端防止重复点击；真正的查询间隔和并发限制仍由后端检查，不能仅依赖按钮禁用。
  if (
    syncing.value ||
    loading.value ||
    !snapshot.value?.service.configured ||
    !snapshot.value.eligible
  )
    return
  const id = ++generation
  syncing.value = true
  error.value = notice.value = ''
  try {
    const result = await syncTracking(props.orderId, props.packageNo)
    if (disposed || id !== generation) return
    snapshot.value = result
    // cached 表示读取缓存，failed 表示本次平台查询失败；HTTP 成功并不一定表示查询更新成功。
    notice.value =
      result.sync_result === 'cached'
        ? '已读取保存的记录；距离上次请求不足查询间隔或已有同步正在进行。'
        : result.sync_result === 'failed'
          ? '本次同步失败，上次成功记录已保留。'
          : '物流记录已更新。'
    emit('updated')
    // 同步后刷新父组件的包裹汇总。
  } catch (reason) {
    if (!disposed && id === generation)
      error.value = reason instanceof Error ? reason.message : '物流同步失败'
  } finally {
    if (!disposed && id === generation) syncing.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  disposed = true
  generation++
})
</script>
<template>
  <section class="logistics-panel" aria-label="物流轨迹">
    <header class="fulfillment-card-title">
      <h3>物流轨迹</h3>
      <button
        class="ops-button"
        :disabled="syncing || loading || !snapshot?.eligible || !snapshot?.service.configured"
        @click="synchronize"
      >
        {{ syncing ? '正在同步…' : '同步物流' }}
      </button>
    </header>
    <p v-if="loading && !snapshot" class="ops-subtle">正在读取物流记录…</p>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }}
      <button class="text-action" :disabled="loading || syncing" @click="load">重读记录</button>
    </p>
    <p v-if="notice" class="logistics-notice" role="status">{{ notice }}</p>
    <template v-if="snapshot">
      <p v-if="!snapshot.service.configured" class="fulfillment-note">
        物流服务尚未配置。当前可办理发货，开通快递100并配置账号后即可同步轨迹。
      </p>
      <p v-else-if="!snapshot.eligible" class="ops-subtle">
        实际交接出库后可同步此运单的物流进度。
      </p>
      <p class="logistics-current">
        <strong>{{ snapshot.status_name }}</strong
        ><span>{{ snapshot.stale ? '上次记录 · 当前进度待核实' : '已保存的平台记录' }}</span>
      </p>
      <p class="ops-subtle">
        数据来源：{{ snapshot.service.provider_name }} · 上次成功同步：{{
          snapshot.last_success_at ? dateTime(snapshot.last_success_at) : '尚未同步'
        }}
      </p>
      <p v-if="snapshot.error_message" class="ops-error" role="alert">
        {{ snapshot.error_message }}
      </p>
      <p v-if="snapshot.next_sync_at" class="ops-subtle">
        下次可请求同步：{{ dateTime(snapshot.next_sync_at) }}（同单至少间隔
        {{ snapshot.service.interval_minutes }} 分钟）
      </p>
      <ol v-if="snapshot.events.length" class="logistics-timeline">
        <li v-for="event in snapshot.events" :key="event.id">
          <time>{{ event.occurred_at ? dateTime(event.occurred_at) : '节点时间待核实' }}</time>
          <p>{{ event.description }}</p>
          <small v-if="event.location">{{ event.location }}</small>
        </li>
      </ol>
      <p v-else-if="!loading" class="ops-subtle">
        暂无可确认的物流节点，不能据此判断已揽收或已签收。
      </p>
      <p v-if="snapshot.events_truncated" class="ops-subtle">
        展示最新 {{ snapshot.events.length }} 条，共 {{ snapshot.event_total }} 条记录。
      </p>
      <p class="ops-subtle">平台签收与人工完成订单分别记录；当前未提供可靠的预计送达时间。</p>
    </template>
  </section>
</template>
