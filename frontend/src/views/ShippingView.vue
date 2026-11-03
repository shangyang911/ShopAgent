<script setup lang="ts">
// 快递履约工作台：运单登记不动库存；仓库确认实物交接后才统一出库。
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  reactive,
  ref,
} from 'vue'
import { ArrowUpRight, Check, PackageCheck, RefreshCw, Search, Truck } from '@lucide/vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import AppPagination from '../shared/components/AppPagination.vue'
import { fetchOrder } from '../features/orders/api/ordersApi'
import type { OrderRecord } from '../features/orders/model/types'
import {
  dispatchShipment,
  fetchShippingWorkbench,
  saveShipment,
} from '../features/shipping/api/shippingApi'
import type { ShippingWorkbench } from '../features/shipping/model/shipping'
import { dateTime, money } from '../shared/commerce'
import MapContainer from '../features/shipping/components/MapContainer.vue'
import { loadAmapApi } from '../features/shipping/config/loadAmapApi'
import { fetchDeliveryRoute } from '../features/shipping/api/shippingApi'
import type { DeliveryRoute } from '../features/shipping/model/shipping'
import { estimateDrivingTime, formatDrivingDuration } from '../features/shipping/model/drivingTime'
import PackageTrackingPanel from '../features/shipping/components/PackageTrackingPanel.vue'
import { trackingStatusNames } from '../features/shipping/api/trackingApi'
import ParcelPanel from '../features/shipping/components/ParcelPanel.vue'
import FreightPanel from '../features/shipping/components/FreightPanel.vue'
import BulkShippingPanel from '../features/shipping/components/BulkShippingPanel.vue'

defineProps<{ username: string }>()
type Tab = 'paid_pending_shipment' | 'in_delivery' | 'completed'
const initial = new URLSearchParams(window.location.search)
const initialTab = initial.get('status')
const tab = ref<Tab>(
  initialTab === 'in_delivery' || initialTab === 'completed' ? initialTab : 'paid_pending_shipment',
)
const keywordDraft = ref(initial.get('keyword') || '')
// 输入框先保存草稿；只有点击查询才写入 keyword 并向后端发起筛选请求。
const keyword = ref(keywordDraft.value)
const page = ref(Math.max(1, Number(initial.get('page')) || 1))
const rows = ref<OrderRecord[]>([])
const selectedBatchIds = ref<number[]>([])
// 批量预览只使用当前列表中实际选中的订单，不把其他页的选择混入本次出库。
const selectedBatchOrders = computed(() =>
  rows.value.filter((order) => selectedBatchIds.value.includes(order.id)),
)
const freightPanel = ref<InstanceType<typeof FreightPanel> | null>(null)
const total = ref(0)
const counts = ref<ShippingWorkbench['counts']>({})
const loading = ref(false)
const error = ref('')
const updatedAt = ref('')
const selectedOrder = ref<OrderRecord | null>(null)
const selectedId = ref<number | null>(Number(initial.get('order_id')) || null)
const detailLoading = ref(false)
const detailError = ref('')
const notice = ref('')
const saving = ref(false)
const dispatching = ref(false)
const draft = reactive({ carrier_name: '', tracking_number: '', remark: '' })
// 保留高德地图与路线能力；它是可独立失败的辅助功能，不参与快递发货校验。
// 快递发货优先呈现运单操作；需要地址核对时再展开辅助地图。
const mapOpen = ref(false)
const selectedRoute = ref<DeliveryRoute | null>(null)
const routeTiming = computed(() => estimateDrivingTime(selectedRoute.value?.duration_seconds))
const routeOrderId = ref<number | null>(null)
const routeLoading = ref(false)
const routeError = ref('')
let routeGeneration = 0
const carriers = [
  '顺丰速运',
  '中通快递',
  '圆通速递',
  '申通快递',
  '韵达快递',
  '邮政EMS',
  '京东物流',
  '极兔速递',
  '德邦快递',
]
const labels: Record<Tab, string> = {
  paid_pending_shipment: '待发货',
  in_delivery: '已发货',
  completed: '已完成',
}
const orderStatusLabel = (status: string) =>
  ({ ...labels, refunded: '已标记退款', cancelled: '已取消', pending_payment: '待付款' })[status] ||
  status
let listGeneration = 0
// 列表、详情和地图各有独立序号：快速切换时，旧请求不能覆盖用户最新的操作。
let detailGeneration = 0
let refreshOnActivate = false
let dispatchKey = ''
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const busy = computed(() => saving.value || dispatching.value)
const historical = computed(
  () =>
    (selectedOrder.value?.status === 'in_delivery' ||
      selectedOrder.value?.status === 'completed') &&
    (!selectedOrder.value?.shipment || selectedOrder.value.shipment.historical),
)
const editable = computed(
  // 交接后运单只读；无运单的历史发货订单允许补录，但不能通过补录再次出库。
  () =>
    !!selectedOrder.value &&
    !detailLoading.value &&
    (selectedOrder.value.status === 'paid_pending_shipment' ||
      (historical.value && !selectedOrder.value.shipment)),
)
const normalizedDraft = () => ({
  carrier_name: draft.carrier_name.trim(),
  tracking_number: draft.tracking_number.trim(),
  remark: draft.remark.trim(),
})
const dirty = computed(() => {
  if (!selectedOrder.value) return false
  const shipment = selectedOrder.value?.shipment
  return (
    JSON.stringify(normalizedDraft()) !==
    JSON.stringify({
      carrier_name: shipment?.carrier_name || '',
      tracking_number: shipment?.tracking_number || '',
      remark: shipment?.remark || '',
    })
  )
})
const canDispatch = computed(
  // 运单必须已保存且没有未提交的修改；是否具有经纬度不参与快递交接校验。
  () =>
    !!selectedOrder.value?.shipment &&
    selectedOrder.value.status === 'paid_pending_shipment' &&
    !selectedOrder.value.aftersale_pending &&
    !dirty.value &&
    !busy.value &&
    !detailLoading.value,
)
const quantity = (order: OrderRecord) => order.items.reduce((sum, item) => sum + item.quantity, 0)
const orderLink = (order: OrderRecord) =>
  `/orders?keyword=${encodeURIComponent(order.order_number)}&order_id=${order.id}`

async function planRoute(order: Pick<OrderRecord, 'id'>) {
  const generation = ++routeGeneration
  mapOpen.value = true
  routeOrderId.value = order.id
  selectedRoute.value = null
  routeLoading.value = true
  routeError.value = ''
  try {
    const route = await fetchDeliveryRoute(order.id)
    if (generation !== routeGeneration) return
    selectedRoute.value = route
  } catch (reason) {
    if (generation === routeGeneration)
      routeError.value = reason instanceof Error ? reason.message : '高德路线加载失败，可稍后重试'
  } finally {
    if (generation === routeGeneration) routeLoading.value = false
  }
}

function syncUrl() {
  const params = new URLSearchParams({ status: tab.value, page: String(page.value) })
  if (keyword.value) params.set('keyword', keyword.value)
  if (selectedId.value) params.set('order_id', String(selectedId.value))
  if (initial.get('issues')) params.set('issues', initial.get('issues')!)
  window.history.replaceState(null, '', `/shipping?${params}`)
}
function fillDraft(order: OrderRecord) {
  Object.assign(draft, {
    carrier_name: order.shipment?.carrier_name || '',
    tracking_number: order.shipment?.tracking_number || '',
    remark: order.shipment?.remark || '',
  })
  dispatchKey = crypto.randomUUID()
}
async function loadOrders() {
  const generation = ++listGeneration
  loading.value = true
  error.value = ''
  syncUrl()
  try {
    const result = await fetchShippingWorkbench({
      status: tab.value,
      keyword: keyword.value,
      page: page.value,
      page_size: 20,
    })
    if (generation !== listGeneration) return
    total.value = result.total
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await loadOrders()
      return
    }
    rows.value = result.items
    counts.value = result.counts
    updatedAt.value = new Date().toISOString()
  } catch (reason) {
    if (generation === listGeneration)
      error.value = reason instanceof Error ? reason.message : '发货列表加载失败'
  } finally {
    if (generation === listGeneration) loading.value = false
  }
}
function refreshFulfillment() {
  void loadOrders()
  void freightPanel.value?.refresh()
}
function refreshSelectedPackages() {
  if (selectedOrder.value) void openOrder(selectedOrder.value.id, true)
  void loadOrders()
}
async function openOrder(id: number, force = false) {
  if (busy.value) return
  if (!force && dirty.value && !window.confirm('离开后将丢弃尚未保存的快递信息，是否继续？')) return
  const generation = ++detailGeneration
  selectedId.value = id
  selectedOrder.value = null
  detailLoading.value = true
  detailError.value = ''
  notice.value = ''
  syncUrl()
  try {
    // 独立读取详情：从订单页跳入时，即使该单不在当前分页，也能准确打开。
    const order = await fetchOrder(id)
    if (generation !== detailGeneration) return
    if (
      !['paid_pending_shipment', 'in_delivery', 'completed'].includes(order.status) &&
      !(order.status === 'refunded' && order.shipment)
    )
      throw new Error('该订单当前不支持发货，请返回订单页核对状态。')
    selectedOrder.value = order
    fillDraft(order)
  } catch (reason) {
    if (generation === detailGeneration)
      detailError.value = reason instanceof Error ? reason.message : '订单详情加载失败'
  } finally {
    if (generation === detailGeneration) detailLoading.value = false
  }
}
function closeDetail() {
  if (busy.value || (dirty.value && !window.confirm('关闭后将丢弃尚未保存的快递信息，是否继续？')))
    return
  detailGeneration++
  selectedOrder.value = null
  selectedId.value = null
  Object.assign(draft, { carrier_name: '', tracking_number: '', remark: '' })
  detailError.value = ''
  notice.value = ''
  syncUrl()
}
function applySearch() {
  selectedBatchIds.value = []
  keyword.value = keywordDraft.value.trim()
  page.value = 1
  void loadOrders()
}
function switchTab(value: Tab) {
  selectedBatchIds.value = []
  tab.value = value
  page.value = 1
  void loadOrders()
}
function changePage(value: number) {
  selectedBatchIds.value = []
  page.value = Math.max(1, Math.min(pageCount.value, value))
  void loadOrders()
}
async function recordShipment() {
  if (!selectedOrder.value || !editable.value || busy.value) return
  const values = normalizedDraft()
  if (
    !values.carrier_name ||
    values.carrier_name.length > 80 ||
    !/^[A-Za-z0-9-]{6,80}$/.test(values.tracking_number) ||
    values.remark.length > 1000
  ) {
    detailError.value =
      '请填写快递公司（最多80字）和6至80位运单号（字母、数字或连字符）；备注最多1000字。'
    return
  }
  if (historical.value && !values.remark) {
    detailError.value = '历史补录请在备注说明信息来源。'
    return
  }
  const generation = detailGeneration
  const order = selectedOrder.value
  saving.value = true
  detailError.value = ''
  notice.value = ''
  try {
    const shipment = await saveShipment(order.id, {
      ...values,
      expected_revision: order.shipment?.revision || 0,
    })
    if (generation !== detailGeneration) return
    order.shipment = shipment
    fillDraft(order)
    notice.value = historical.value
      ? '历史快递信息已补录，库存保持不变。'
      : '快递信息已保存。实物交接后，请确认交接出库。'
    await loadOrders()
  } catch (reason) {
    if (generation === detailGeneration)
      detailError.value = reason instanceof Error ? reason.message : '快递信息保存失败'
  } finally {
    saving.value = false
  }
}
async function confirmDispatch() {
  if (!canDispatch.value || !selectedOrder.value?.shipment) return
  const order = selectedOrder.value
  const shipment = order.shipment!
  if (
    !window.confirm(
      `确认已将订单 ${order.order_number} 的 ${quantity(order)} 件商品及下方包裹清单中的全部包裹实际交给快递？\n主运单：${shipment.carrier_name} ${shipment.tracking_number}\n确认后将结束库存预留并登记实物出库。`,
    )
  )
    return
  const generation = detailGeneration
  dispatching.value = true
  detailError.value = ''
  notice.value = ''
  try {
    // 失败重试沿用同一键；服务端同时核对运单版本，避免按过期信息交接。
    const result = await dispatchShipment(order.id, shipment.revision, dispatchKey)
    if (generation !== detailGeneration) return
    selectedOrder.value = result
    fillDraft(result)
    notice.value = '交接出库已登记。快递揽收与运输进度尚未同步。'
    await loadOrders()
    void freightPanel.value?.refresh()
  } catch (reason) {
    if (generation === detailGeneration)
      detailError.value = reason instanceof Error ? reason.message : '交接出库失败，可核对后重试'
  } finally {
    dispatching.value = false
  }
}
function restoreLocation() {
  // KeepAlive 隐藏页面仍有全局监听；其他页面的后退操作不能被这里改写成 /shipping。
  if (window.location.pathname !== '/shipping') return
  if (busy.value) return
  const params = new URLSearchParams(window.location.search)
  const id = Number(params.get('order_id')) || null
  const status = params.get('status')
  if (status === 'in_delivery' || status === 'completed' || status === 'paid_pending_shipment')
    tab.value = status
  keyword.value = keywordDraft.value = params.get('keyword') || ''
  page.value = Math.max(1, Number(params.get('page')) || 1)
  if (id && id !== selectedId.value) void openOrder(id)
  else if (!id && selectedId.value) closeDetail()
  void loadOrders()
}
onMounted(() => {
  // 站内导航进入发货页时也预热 SDK，展开地图直接复用下载结果。
  void loadAmapApi().catch(() => {})
  void loadOrders()
  if (selectedId.value) void openOrder(selectedId.value, true)
  window.addEventListener('popstate', restoreLocation)
})
onDeactivated(() => {
  routeGeneration++
  routeLoading.value = false
  refreshOnActivate = true
  listGeneration++
  detailGeneration++
  loading.value = detailLoading.value = false
})
onActivated(() => {
  if (!refreshOnActivate) return
  refreshOnActivate = false
  const params = new URLSearchParams(window.location.search)
  const id = Number(params.get('order_id')) || null
  const status = params.get('status')
  if (status === 'paid_pending_shipment' || status === 'in_delivery' || status === 'completed')
    tab.value = status
  if (id && id !== selectedId.value) void openOrder(id)
  else if (selectedId.value && !dirty.value && !busy.value) void openOrder(selectedId.value, true)
  void loadOrders()
})
onBeforeUnmount(() => {
  routeGeneration++
  listGeneration++
  detailGeneration++
  window.removeEventListener('popstate', restoreLocation)
})
</script>

<template>
  <OperationsShell active-page="shipping" title="发货与物流" :username="username">
    <header class="ops-heading">
      <div>
        <p class="ops-eyebrow">ORDER FULFILLMENT</p>
        <h1>发货与物流</h1>
        <p>核对配货、登记运单，在实物交接后确认出库。</p>
      </div>
      <button class="ops-button" :disabled="loading" @click="loadOrders">
        <RefreshCw :size="15" />刷新列表
      </button>
    </header>
    <div class="fulfillment-metrics" aria-label="订单履约概况">
      <button
        v-for="state in ['paid_pending_shipment', 'in_delivery', 'completed'] as const"
        :key="state"
        :class="{ active: tab === state }"
        @click="switchTab(state)"
      >
        <span>{{ labels[state] }}</span
        ><strong>{{ counts[state] ?? (updatedAt ? 0 : '—') }}</strong
        ><small>{{
          state === 'paid_pending_shipment'
            ? '核对运单 · 等待交接'
            : state === 'in_delivery'
              ? '已登记出库 · 可查运单'
              : '人工确认的订单完成'
        }}</small>
      </button>
    </div>
    <FreightPanel ref="freightPanel" />
    <section class="fulfillment-map-panel" aria-label="高德地图与路线参考">
      <header class="fulfillment-card-title">
        <div>
          <h2>高德地图与路线参考</h2>
          <p class="ops-subtle">选择订单查看发货点至收件地址的路线，辅助核对位置。</p>
        </div>
        <button class="ops-button" :aria-expanded="mapOpen" @click="mapOpen = !mapOpen">
          {{ mapOpen ? '收起地图' : '展开地图' }}
        </button>
      </header>
      <template v-if="mapOpen">
        <p v-if="routeLoading" class="ops-subtle" role="status">正在规划高德路线…</p>
        <p v-if="routeError" class="ops-error" role="alert">
          {{ routeError }}
          <button v-if="routeOrderId" class="text-action" @click="planRoute({ id: routeOrderId })">
            重试路线
          </button>
        </p>
        <div v-if="selectedRoute" class="route-summary">
          <strong>{{ selectedRoute.order_number }}</strong
          ><span>驾车参考 {{ (selectedRoute.distance_meters / 1000).toFixed(1) }} km</span
          ><span>高德驾驶约 {{ formatDrivingDuration(selectedRoute.duration_seconds) }}</span>
          <template v-if="routeTiming">
            <span
              >途中休息 {{ routeTiming.restCount }} 次 ·
              {{ formatDrivingDuration(routeTiming.restSeconds) }}</span
            >
            <span class="route-total-time"
              >含休息约 {{ formatDrivingDuration(routeTiming.totalSeconds) }}</span
            >
          </template>
        </div>
        <p v-if="selectedRoute" class="ops-subtle">
          按单人驾驶、每连续驾驶 4 小时途中休息至少 20 分钟估算；到达终点不另计休息，1 天按 24
          小时换算。 未计入夜间限行、装卸及其他适用驾驶限制。
          <a
            href="https://www.samr.gov.cn/zljds/zcfg/art/2023/art_5c212e15369443b3b2bea4e17a1c565b.html"
            target="_blank"
            rel="noopener noreferrer"
            >休息规则依据（第六十二条第七项）</a
          >
        </p>
      </template>
      <KeepAlive>
        <MapContainer v-if="mapOpen" :route="selectedRoute" />
      </KeepAlive>
      <p v-if="mapOpen" class="ops-subtle">
        路线与耗时为高德驾车参考，不代表快递运输轨迹或预计送达时间。地图不可用时仍可登记运单、确认交接出库。
      </p>
    </section>
    <form class="ops-filterbar" @submit.prevent="applySearch">
      <label class="grow"
        >搜索订单或运单<input
          v-model="keywordDraft"
          maxlength="180"
          placeholder="订单号、收件人、手机号、运单号" /></label
      ><button class="ops-button primary"><Search :size="15" />查询</button>
    </form>
    <div class="ops-meta">
      <span>{{ labels[tab] }} · 共 {{ total }} 单</span
      ><span>{{ updatedAt ? `更新于 ${dateTime(updatedAt)}` : '正在读取订单' }}</span>
    </div>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }}<button class="text-action" @click="loadOrders">重试</button>
    </p>
    <BulkShippingPanel
      v-if="tab === 'paid_pending_shipment'"
      :orders="selectedBatchOrders"
      @changed="refreshFulfillment"
    />
    <div class="fulfillment-layout" :class="{ 'has-detail': selectedId }">
      <section class="fulfillment-list" :aria-busy="loading" aria-label="发货订单列表">
        <p v-if="loading && !rows.length" class="module-empty">正在加载发货订单…</p>
        <p v-else-if="!rows.length && !error" class="module-empty">
          当前条件下没有{{ labels[tab] }}订单。
        </p>
        <article
          v-for="order in rows"
          :key="order.id"
          class="fulfillment-card"
          :class="{ selected: selectedId === order.id }"
        >
          <div class="fulfillment-card-title">
            <label v-if="tab === 'paid_pending_shipment'" class="ops-subtle">
              <input
                v-model="selectedBatchIds"
                type="checkbox"
                :value="order.id"
                :disabled="busy || order.aftersale_pending"
                :aria-label="`选择订单 ${order.order_number} 批量发货`"
              />批量选择
            </label>
            <button class="text-action" :disabled="busy" @click="openOrder(order.id)">
              {{ order.order_number }}</button
            ><span class="fulfillment-badge">{{ orderStatusLabel(order.status) }}</span>
          </div>
          <p class="fulfillment-recipient">
            {{ order.recipient_name }} · {{ order.recipient_phone }}
          </p>
          <p class="ops-subtle">
            {{ order.province }}{{ order.city }}{{ order.district }}{{ order.address_detail }}
          </p>
          <p class="fulfillment-meta">
            {{ order.items.length }} 种商品 · {{ quantity(order) }} 件
            <span>应收 {{ money(order.total) }}</span>
          </p>
          <p class="ops-subtle">确认收款：{{ dateTime(order.paid_at) }}</p>
          <p v-if="order.aftersale_pending" class="fulfillment-note">售后处理中，暂停交接出库</p>
          <p v-if="Number(order.refunded_amount) > 0" class="ops-subtle">
            已登记退款 {{ money(order.refunded_amount) }}，履约状态分别保留
          </p>
          <p v-if="order.logistics_summary" class="ops-subtle">
            {{ order.logistics_summary.summary }} · {{ order.logistics_summary.signed_count }}/{{
              order.logistics_summary.package_count
            }}
            个包裹
          </p>
          <div v-if="order.shipment" class="fulfillment-waybill">
            <Truck :size="15" /><span
              >{{ order.shipment.carrier_name }}
              <strong>{{ order.shipment.tracking_number }}</strong></span
            >
          </div>
          <p v-else class="fulfillment-pending">
            {{
              order.status === 'paid_pending_shipment'
                ? '待登记快递公司与运单号'
                : '历史快递信息待补录'
            }}
          </p>
          <p v-if="order.shipment?.tracking" class="ops-subtle">
            首包裹物流：{{
              trackingStatusNames[order.shipment.tracking.status] || '暂无可确认轨迹'
            }}
            ·
            {{
              order.shipment.tracking.last_success_at
                ? `同步于 ${dateTime(order.shipment.tracking.last_success_at)}`
                : '尚未成功同步'
            }}
          </p>
          <div class="fulfillment-actions">
            <button
              v-if="order.status !== 'completed'"
              class="ops-button"
              @click="planRoute(order)"
            >
              {{ routeLoading && routeOrderId === order.id ? '规划中…' : '高德路线' }}
            </button>
            <button class="ops-button" :disabled="busy" @click="openOrder(order.id)">
              {{
                order.status === 'paid_pending_shipment'
                  ? '办理发货'
                  : order.shipment
                    ? '查看发货记录'
                    : '补录快递信息'
              }}</button
            ><a class="ops-button" :href="orderLink(order)"
              >订单 / 配货单<ArrowUpRight :size="13"
            /></a>
          </div>
        </article>
        <AppPagination
          v-if="total > 20"
          :page="page"
          :page-size="20"
          :page-count="pageCount"
          :total="total"
          :loading="loading"
          @change="changePage"
        />
      </section>
      <aside
        v-if="selectedId"
        class="fulfillment-detail"
        aria-label="发货详情"
        :aria-busy="detailLoading"
      >
        <div class="fulfillment-card-title">
          <h2>发货详情</h2>
          <button class="text-action" :disabled="busy" @click="closeDetail">关闭</button>
        </div>
        <p v-if="detailLoading" class="module-empty">正在核对订单…</p>
        <p v-if="detailError" class="ops-error" role="alert">
          {{ detailError }}
          <button v-if="!selectedOrder" class="text-action" @click="openOrder(selectedId, true)">
            重新读取
          </button>
        </p>
        <p v-if="notice" class="fulfillment-success" role="status">
          <Check :size="15" />{{ notice }}
        </p>
        <template v-if="selectedOrder">
          <div class="fulfillment-detail-heading">
            <strong>{{ selectedOrder.order_number }}</strong
            ><span>{{ orderStatusLabel(selectedOrder.status) }}</span>
          </div>
          <p>{{ selectedOrder.recipient_name }} · {{ selectedOrder.recipient_phone }}</p>
          <p class="ops-subtle">
            {{ selectedOrder.country }} {{ selectedOrder.province }}{{ selectedOrder.city
            }}{{ selectedOrder.district }}{{ selectedOrder.address_detail }}
          </p>
          <ul class="fulfillment-items">
            <li v-for="item in selectedOrder.items" :key="item.id">
              <span
                >{{ item.product_name
                }}<small v-if="item.sku_label"> · {{ item.sku_label }}</small></span
              ><strong>× {{ item.quantity }}</strong>
            </li>
          </ul>
          <p v-if="selectedOrder.note" class="fulfillment-note">
            下单备注：{{ selectedOrder.note }}
          </p>
          <a class="text-action fulfillment-order-link" :href="orderLink(selectedOrder)"
            >查看订单、操作时间线及打印配货单<ArrowUpRight :size="13"
          /></a>
          <form class="fulfillment-form" @submit.prevent="recordShipment">
            <h3>{{ historical && !selectedOrder.shipment ? '补录历史快递信息' : '快递信息' }}</h3>
            <p v-if="historical && !selectedOrder.shipment" class="fulfillment-note">
              补录仅补齐记录，不会再次出库。请注明信息来源。
            </p>
            <fieldset :disabled="!editable || busy">
              <label
                >快递公司<input
                  v-model="draft.carrier_name"
                  list="fulfillment-carriers"
                  maxlength="80"
                  required
                  placeholder="选择或填写快递公司" /></label
              ><datalist id="fulfillment-carriers">
                <option v-for="carrier in carriers" :key="carrier" :value="carrier" /></datalist
              ><label
                >运单号<input
                  v-model="draft.tracking_number"
                  minlength="6"
                  maxlength="80"
                  required
                  autocomplete="off"
                  placeholder="输入或用扫码枪扫描运单号" /></label
              ><label
                >{{ historical ? '补录备注 / 信息来源' : '发货备注'
                }}<textarea
                  v-model="draft.remark"
                  maxlength="1000"
                  rows="3"
                  :required="historical"
                  placeholder="记录交接注意事项或信息来源"
                />
              </label>
            </fieldset>
            <button
              v-if="editable"
              class="ops-button"
              :disabled="busy || (selectedOrder.shipment != null && !dirty)"
            >
              {{ saving ? '正在保存…' : historical ? '保存历史补录' : '保存快递信息' }}
            </button>
          </form>
          <div v-if="selectedOrder.status === 'paid_pending_shipment'" class="fulfillment-handover">
            <PackageCheck :size="21" />
            <div>
              <strong>实物交接后确认出库</strong>
              <p>
                {{
                  selectedOrder.aftersale_pending
                    ? '售后处理中，请先完成或撤销售后，再确认实物交接。'
                    : dirty
                      ? '快递信息有未保存修改，请先保存。'
                      : selectedOrder.shipment
                        ? '请核对运单和全部商品，确认已交给快递。'
                        : '请先保存快递公司和运单号。'
                }}
              </p>
            </div>
            <button class="ops-button primary" :disabled="!canDispatch" @click="confirmDispatch">
              {{ dispatching ? '正在出库…' : '确认交接出库' }}
            </button>
          </div>
          <dl v-if="selectedOrder.shipment" class="fulfillment-audit">
            <dt>信息登记</dt>
            <dd>
              {{ selectedOrder.shipment.updated_by }} ·
              {{ dateTime(selectedOrder.shipment.updated_at) }}
            </dd>
            <dt>交接出库</dt>
            <dd>
              {{
                selectedOrder.shipment.dispatched_at
                  ? `${selectedOrder.shipment.dispatched_by || '历史记录'} · ${dateTime(selectedOrder.shipment.dispatched_at)}`
                  : selectedOrder.shipment.historical
                    ? '历史交接时间待核实'
                    : '尚未确认交接'
              }}
            </dd>
          </dl>
          <ParcelPanel
            :key="`parcels:${selectedOrder.id}:${selectedOrder.shipment?.revision || 0}:${selectedOrder.status}`"
            :order="selectedOrder"
            :disabled="busy || dirty"
            @changed="refreshSelectedPackages"
            @settled="freightPanel?.refresh()"
          />
          <PackageTrackingPanel
            :key="`${selectedOrder.id}:${selectedOrder.shipment?.revision || 0}:${selectedOrder.shipment?.dispatched_at || 'pending'}`"
            :order-id="selectedOrder.id"
          />
        </template>
      </aside>
    </div>
  </OperationsShell>
</template>
