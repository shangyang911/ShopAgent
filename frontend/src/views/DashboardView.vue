<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowUpRight, RefreshCw } from '@lucide/vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import { request } from '../shared/api/httpClient'
import { query } from '../shared/api/query'
import type { Dashboard } from '../features/dashboard/model/types'
import { addDays, dateTime, money, shanghaiToday } from '../shared/commerce'

defineProps<{ username: string }>()
const today = ref(shanghaiToday())
const start = ref(today.value)
const end = ref(today.value)
const preset = ref('今日')
const granularity = ref('day')
const data = ref<Dashboard | null>(null)
const loading = ref(false)
const error = ref('')
let requestId = 0
let timer: ReturnType<typeof setInterval> | undefined
const cards = [
  {
    key: 'confirmed_receipt_amount',
    title: '已确认收款（含运费）',
    field: 'paid_at',
    amount: true,
    help: '区间内手工确认收款记录之和。仅代表后台登记，未与银行或支付机构对账。',
  },
  {
    key: 'confirmed_receipt_order_count',
    title: '确认收款订单数',
    field: 'paid_at',
    amount: false,
    help: '按首次成功确认收款时间统计，每单只计一次。',
  },
  {
    key: 'created_order_count',
    title: '新建订单数',
    field: 'created_at',
    amount: false,
    help: '按创建时间统计，包含随后取消的订单。',
  },
  {
    key: 'completed_order_count',
    title: '完成订单数',
    field: 'completed_at',
    amount: false,
    help: '按首次完成时间统计，不使用最后更新日期推算。',
  },
  {
    key: 'average_confirmed_order_amount',
    title: '平均确认收款订单金额',
    field: 'paid_at',
    amount: true,
    help: '确认收款额 ÷ 确认收款订单数；无收款订单时显示 —。',
  },
]
const amountMax = computed(() =>
  Math.max(1, ...(data.value?.trend.map((row) => Number(row.confirmed_receipt_amount ?? 0)) ?? [])),
)
const countMax = computed(() =>
  Math.max(
    1,
    ...(data.value?.trend.flatMap((row) => [
      row.confirmed_receipt_order_count ?? 0,
      row.created_order_count ?? 0,
      row.completed_order_count ?? 0,
    ]) ?? []),
  ),
)
const empty = computed(
  () =>
    data.value &&
    Object.values(data.value.metrics).every(
      (metric) => metric.value == null || Number(metric.value) === 0,
    ),
)

async function load() {
  today.value = shanghaiToday()
  if (preset.value !== '自定义') {
    end.value = preset.value === '昨日' ? addDays(today.value, -1) : today.value
    start.value =
      preset.value === '近 7 天'
        ? addDays(today.value, -6)
        : preset.value === '近 30 天'
          ? addDays(today.value, -29)
          : end.value
  }
  const id = ++requestId
  error.value = ''
  if (!start.value || !end.value || start.value > end.value) {
    error.value = '请选择有效的起止日期'
    return
  }
  loading.value = true
  try {
    const result = await request<Dashboard>(
      `/dashboard?${query({ start_date: start.value, end_date: addDays(end.value, 1), granularity: granularity.value })}`,
    )
    if (id === requestId) data.value = result
  } catch (reason) {
    if (id === requestId)
      error.value = reason instanceof Error ? reason.message : '经营数据加载失败'
  } finally {
    if (id === requestId) loading.value = false
  }
}

function choosePreset(value: string) {
  preset.value = value
  const current = shanghaiToday()
  end.value = value === '昨日' ? addDays(current, -1) : current
  start.value =
    value === '近 7 天'
      ? addDays(current, -6)
      : value === '近 30 天'
        ? addDays(current, -29)
        : end.value
  void load()
}

function applyCustom() {
  preset.value = '自定义'
  void load()
}

function drill(field: string, range?: { start_at: string; end_at: string }) {
  if (!data.value) return '#'
  return `/orders?${query({
    time_field: field,
    start_at: range?.start_at ?? data.value.period_start,
    end_at: range?.end_at ?? data.value.effective_end,
    as_of: data.value.as_of,
  })}`
}

function visibilityChanged() {
  if (timer) clearInterval(timer)
  if (!document.hidden) {
    void load()
    timer = setInterval(() => {
      if (preset.value !== '自定义') choosePreset(preset.value)
      else void load()
    }, 60000)
  }
}
onMounted(() => {
  visibilityChanged()
  document.addEventListener('visibilitychange', visibilityChanged)
})
onBeforeUnmount(() => {
  requestId++
  if (timer) clearInterval(timer)
  document.removeEventListener('visibilitychange', visibilityChanged)
})
</script>

<template>
  <OperationsShell active-page="overview" title="经营概览" :username="username">
    <div class="ops-heading">
      <div>
        <p class="ops-eyebrow">STORE OVERVIEW</p>
        <h1>经营概览</h1>
        <p>看清经营结果，把需要处理的事放在眼前。</p>
      </div>
      <button class="ops-button" :disabled="loading" @click="load">
        <RefreshCw :size="16" :class="{ spinning: loading }" /> 刷新数据
      </button>
    </div>
    <form class="ops-filterbar" @submit.prevent="applyCustom">
      <div class="ops-segments">
        <button
          v-for="value in ['今日', '昨日', '近 7 天', '近 30 天']"
          :key="value"
          type="button"
          :class="{ active: preset === value }"
          @click="choosePreset(value)"
        >
          {{ value }}
        </button>
      </div>
      <label
        >开始日期<input
          v-model="start"
          type="date"
          :max="today"
          required
          @input="preset = '自定义'" /></label
      ><span>至</span
      ><label
        >结束日期<input
          v-model="end"
          type="date"
          :min="start"
          :max="today"
          required
          @input="preset = '自定义'" /></label
      ><button class="ops-button" :disabled="loading">应用</button
      ><small>Asia/Shanghai · 人民币</small>
    </form>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }}<span v-if="data">；保留上次成功数据，更新时间 {{ dateTime(data.as_of) }}。</span
      ><button class="text-action" @click="load">重试</button>
    </p>
    <p v-if="loading && !data" class="ops-empty" role="status">正在计算经营数据…</p>
    <template v-if="data">
      <div class="ops-meta">
        <span
          >统计范围 {{ dateTime(data.period_start) }} 至
          {{ dateTime(data.effective_end) }}（不含截止时刻）</span
        ><span>{{ loading ? '正在刷新…' : '更新于 ' + dateTime(data.as_of) }}</span>
      </div>
      <!-- 净确认收款只扣本期实际登记退款，不等同于利润或支付机构余额。 -->
      <section class="business-panel" aria-label="退款与净确认收款">
        <p>
          本期已登记实际退款
          <strong>{{ money(data.metrics.recorded_refund_amount?.value) }}</strong> · 本期净确认收款
          <strong>{{ money(data.metrics.net_confirmed_amount?.value) }}</strong>
        </p>
        <p class="ops-subtle">
          按实际业务发生时间统计；历史仅标记退款但没有金额的订单不推算。净确认收款不代表利润。
        </p>
      </section>
      <div class="metric-grid" :aria-busy="loading">
        <a
          v-for="card in cards"
          :key="card.key"
          :href="drill(card.field)"
          class="metric-card"
          :title="card.help"
        >
          <div class="metric-title">{{ card.title }} <ArrowUpRight :size="16" /></div>
          <strong>{{
            card.amount
              ? money(data.metrics[card.key]?.value)
              : (data.metrics[card.key]?.value ?? '—')
          }}</strong>
          <small
            >{{ data.metrics[card.key]?.change_label }} <span>较前一等长区间同一进度</span></small
          >
          <small v-if="data.metrics[card.key]?.reliability !== 'complete'" class="warning-text">{{
            data.metrics[card.key]?.reliability === 'unknown'
              ? '无可靠数据'
              : '仅含覆盖起点后的可靠记录'
          }}</small>
          <small
            v-else-if="
              card.key === 'average_confirmed_order_amount' && data.metrics[card.key]?.value == null
            "
            >本期无确认收款订单</small
          >
        </a>
      </div>
      <div class="ops-notice">
        完整事件数据从
        {{ dateTime(data.data_coverage_start) }}
        开始覆盖；此前未核实的业务时间保持未知。收款为后台手工确认，未与银行对账。
        <details>
          <summary>指标与对比口径</summary>
          <p v-for="card in cards" :key="card.key">{{ card.title }}：{{ card.help }}</p>
          <p>
            对比范围 {{ dateTime(data.comparison_start) }} 至
            {{ dateTime(data.comparison_end) }}。基数为 0 时显示“新增”或“持平”。
          </p>
        </details>
      </div>
      <p v-if="empty" class="ops-empty compact">
        本期暂无已登记业务；“—”表示无法可靠计算，与 0 不同。
      </p>
      <section class="ops-panel">
        <div class="ops-panel-heading">
          <div>
            <h2>当前待办</h2>
            <p>按当前状态统计，独立于上方历史日期。</p>
          </div>
          <small>未发货超时：确认收款满 24 个自然小时</small>
        </div>
        <div class="todo-grid">
          <a href="/orders?status=paid_pending_shipment"
            ><span>待发货</span><strong>{{ data.todos.pending_shipment }}</strong
            ><ArrowUpRight :size="18"
          /></a>
          <a href="/orders?status=paid_pending_shipment&overdue=true"
            ><span>超时未发货</span
            ><strong class="warning-text">{{ data.todos.overdue_shipment }}</strong
            ><ArrowUpRight :size="18"
          /></a>
          <a href="/inventory?severity=low_stock"
            ><span>低库存</span><strong class="warning-text">{{ data.todos.low_stock }}</strong
            ><ArrowUpRight :size="18"
          /></a>
          <a href="/inventory?severity=out_of_stock"
            ><span>缺货</span><strong class="danger-text">{{ data.todos.out_of_stock }}</strong
            ><ArrowUpRight :size="18"
          /></a>
        </div>
        <a
          v-if="data.todos.unknown_payment_time"
          class="ops-inline-link"
          href="/orders?status=paid_pending_shipment&time_field=paid_at&unknown_time=true"
          >另有 {{ data.todos.unknown_payment_time }} 单付款时间待核实，未计算超时 →</a
        >
      </section>
      <div class="ops-panel-heading">
        <h2>经营趋势</h2>
        <label class="ops-inline-field"
          >统计粒度<select v-model="granularity" @change="load">
            <option value="day">日</option>
            <option value="week">周</option>
            <option value="month">月</option>
          </select></label
        >
      </div>
      <div class="trend-grid">
        <section class="ops-panel">
          <h3>确认收款额 <small>元 · 含运费</small></h3>
          <p class="ops-subtle">点击柱形进入对应收款时间明细</p>
          <div class="chart-scroll">
            <div
              class="bar-chart"
              :style="{ minWidth: `${Math.max(320, data.trend.length * 32)}px` }"
            >
              <a
                v-for="point in data.trend"
                :key="point.label"
                :href="drill('paid_at', point)"
                class="chart-column"
                :title="`${point.label}：${money(point.confirmed_receipt_amount)}${point.reliability !== 'complete' ? '（数据覆盖不完整）' : ''}`"
                ><span class="chart-value">{{
                  point.confirmed_receipt_amount == null ? '?' : point.confirmed_receipt_amount
                }}</span
                ><span
                  class="chart-bar"
                  :class="{ unknown: point.confirmed_receipt_amount == null }"
                  :style="{
                    height: `${point.confirmed_receipt_amount == null ? 5 : Math.max(2, (Number(point.confirmed_receipt_amount) / amountMax) * 145)}px`,
                  }"
                ></span
                ><small>{{ point.label.slice(5) }}</small></a
              >
            </div>
          </div>
        </section>
        <section class="ops-panel">
          <h3>订单量 <small>单</small></h3>
          <div class="chart-legend"><span>● 收款</span><span>● 新建</span><span>● 完成</span></div>
          <div class="chart-scroll">
            <div
              class="bar-chart"
              :style="{ minWidth: `${Math.max(320, data.trend.length * 44)}px` }"
            >
              <div v-for="point in data.trend" :key="point.label" class="chart-column">
                <div class="chart-bar-group">
                  <a
                    v-for="(series, index) in [
                      { key: 'confirmed_receipt_order_count' as const, field: 'paid_at' },
                      { key: 'created_order_count' as const, field: 'created_at' },
                      { key: 'completed_order_count' as const, field: 'completed_at' },
                    ]"
                    :key="series.key"
                    :href="drill(series.field, point)"
                    :title="`${point.label} ${cards[index + 1]?.title}：${point[series.key] ?? '无可靠数据'}`"
                    class="chart-bar"
                    :class="[`series-${index}`, { unknown: point[series.key] == null }]"
                    :style="{
                      height: `${point[series.key] == null ? 5 : Math.max(2, (Number(point[series.key]) / countMax) * 145)}px`,
                    }"
                  ></a>
                </div>
                <small>{{ point.label.slice(5) }}</small>
              </div>
            </div>
          </div>
        </section>
      </div>
    </template>
  </OperationsShell>
</template>
