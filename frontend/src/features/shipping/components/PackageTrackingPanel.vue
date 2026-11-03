<script setup lang="ts">
// 选择包裹后按包裹号重新挂载轨迹面板，异步响应不会覆盖另一包裹。
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchPackageTracking } from '../api/trackingApi'
import LogisticsPanel from './LogisticsPanel.vue'

const props = defineProps<{ orderId: number }>()
const data = ref<Awaited<ReturnType<typeof fetchPackageTracking>> | null>(null)
const selected = ref(1)
const error = ref('')
let alive = true
let generation = 0
async function load() {
  const sequence = ++generation
  try {
    const value = await fetchPackageTracking(props.orderId)
    if (!alive || sequence !== generation) return
    data.value = value
    error.value = ''
    if (!value.items.some((item) => item.package_no === selected.value))
      selected.value = value.items[0]?.package_no || 1
  } catch (reason) {
    if (alive && sequence === generation)
      error.value = reason instanceof Error ? reason.message : '包裹汇总读取失败'
  }
}
onMounted(load)
onBeforeUnmount(() => {
  alive = false
  generation++
})
</script>
<template>
  <section aria-label="包裹物流汇总">
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load">重试</button>
    </p>
    <template v-if="data">
      <p>{{ data.summary }} · {{ data.signed_count }}/{{ data.package_count }} 个包裹显示签收</p>
      <label v-if="data.items.length > 1"
        >查看包裹<select v-model="selected">
          <option v-for="item in data.items" :key="item.package_no" :value="item.package_no">
            包裹 #{{ item.package_no }} · {{ item.tracking_number }} · {{ item.status_name }}
          </option>
        </select></label
      >
      <!-- 汇总与订单人工完成分别展示；没有节点时不猜测已签收。 -->
      <LogisticsPanel
        :key="`${orderId}:${selected}`"
        :order-id="orderId"
        :package-no="selected"
        @updated="load"
      />
    </template>
  </section>
</template>
