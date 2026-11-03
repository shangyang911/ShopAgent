<script setup lang="ts">
// 运费报表区分客户支付的运费和已登记的实际支出；未结算包裹不能按零成本计算。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { request } from '../../../shared/api/httpClient'
import type { FreightReport } from '../model/parcels'
import { downloadCsv } from '../../../shared/api/download'
import { money } from '../../../shared/commerce'

const report = ref<FreightReport | null>(null)
const filters = reactive({ start: '', end: '', carrier: '' })
const loading = ref(false)
const error = ref('')
const exporting = ref(false)
const appliedQuery = ref<string | null>(null)
let generation = 0
let disposed = false
const changedFilters = computed(
  () => appliedQuery.value !== null && appliedQuery.value !== params(),
)
function params() {
  const query = new URLSearchParams()
  if (filters.start)
    query.set('start_at', new Date(`${filters.start}T00:00:00+08:00`).toISOString())
  if (filters.end) {
    const date = new Date(`${filters.end}T00:00:00+08:00`)
    date.setUTCDate(date.getUTCDate() + 1)
    query.set('end_at', date.toISOString())
  }
  if (filters.carrier.trim()) query.set('carrier', filters.carrier.trim())
  return query.toString()
}
async function refresh() {
  const id = ++generation
  loading.value = true
  error.value = ''
  try {
    // 冻结本次查询条件；结算后触发的新刷新不能被较慢的旧结果覆盖。
    const query = params()
    const value = await request<FreightReport>(`/shipping/freight-report?${query}`)
    if (disposed || id !== generation) return false
    report.value = value
    appliedQuery.value = query
    return true
  } catch (reason) {
    if (!disposed && id === generation)
      error.value = reason instanceof Error ? reason.message : '运费统计加载失败'
    return false
  } finally {
    if (!disposed && id === generation) loading.value = false
  }
}
async function exportCsv() {
  if (loading.value || exporting.value) return
  exporting.value = true
  error.value = ''
  try {
    // 改条件后直接导出也先更新预览，避免屏幕和 CSV 各自使用不同筛选。
    if (appliedQuery.value !== params() && !(await refresh())) return
    if (disposed) return
    await downloadCsv(`/shipping/freight-report/export?${appliedQuery.value}`, '实际运费明细.csv')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '运费导出失败'
  } finally {
    if (!disposed) exporting.value = false
  }
}
defineExpose({ refresh })
onMounted(refresh)
onBeforeUnmount(() => {
  disposed = true
  generation++
})
</script>

<template>
  <details class="freight-panel">
    <summary>实际运费统计与导出</summary>
    <p class="ops-subtle">
      按交接日期（北京时间）统计，含截止日。未知交接日期的历史包裹仅计入未限定日期的汇总。未结算运费不计作零元。
    </p>
    <form class="ops-filterbar" @submit.prevent="refresh">
      <label>开始日期<input v-model="filters.start" type="date" /></label>
      <label>截止日期<input v-model="filters.end" type="date" /></label>
      <label
        >快递公司<input v-model="filters.carrier" maxlength="80" placeholder="留空统计全部"
      /></label>
      <button class="ops-button" :disabled="loading">查询</button
      ><button class="ops-button" type="button" :disabled="loading || exporting" @click="exportCsv">
        导出包裹运费 CSV
      </button>
    </form>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="changedFilters" class="ops-subtle">
      筛选条件已修改，当前仍显示上次查询；导出前会先更新当前条件的预览。
    </p>
    <template v-if="report">
      <p aria-live="polite">
        {{ report.order_count }} 单 / {{ report.package_count }} 个包裹 · 已登记实际运费
        <strong>{{ money(report.actual_cost) }}</strong> · 待结算 {{ report.unsettled_packages }} 个
      </p>
      <p class="ops-subtle">
        相关订单向客户收取运费 {{ money(report.customer_shipping_fee) }}（每单仅计一次）；{{
          report.complete
            ? '当前范围包裹均已登记实际运费。'
            : '仍有包裹待结算，当前支出合计不代表最终成本。'
        }}
      </p>
      <ul>
        <li v-for="group in report.groups" :key="group.carrier">
          {{ group.carrier_name }}：{{ group.packages }} 个包裹 · 已登记运费
          {{ money(group.actual_cost) }} · 待结算 {{ group.unsettled_packages }} 个
        </li>
      </ul>
    </template>
  </details>
</template>

<style scoped>
.freight-panel {
  padding: 20px;
  border: 1px solid var(--border, #e4e7ec);
  border-radius: 12px;
  margin: 20px 0;
}
summary {
  cursor: pointer;
  font-weight: 650;
}
</style>
