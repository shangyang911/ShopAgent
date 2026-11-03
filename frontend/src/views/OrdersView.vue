<script setup lang="ts">
// [订单工作台] 组合查询、订单动作与留痕、不可变单据及明确的打印请求。
import AfterSalesPanel from '../features/aftersales/components/AfterSalesPanel.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronRight, LogOut, Package, Plus, Trash2, X } from '@lucide/vue'
import LanguageSwitcher from '../shared/components/LanguageSwitcher.vue'
import AppSidebar from '../shared/components/AppSidebar.vue'
import AppPagination from '../shared/components/AppPagination.vue'
import { fetchCustomerAddresses, fetchCustomers } from '../features/customers/api/customersApi'
import type { AddressRecord, CustomerRecord } from '../features/customers/model/types'
import {
  createOrder,
  fetchOrder,
  fetchOrders,
  updateOrderStatus,
} from '../features/orders/api/ordersApi'
import type {
  OrderRecord,
  OrderStatus,
  OrderFilterStatus,
  OrderFilters,
} from '../features/orders/model/types'
import { API_BASE } from '../shared/api/config'
import { request } from '../shared/api/httpClient'
import type { Page } from '../shared/api/types'
import type { TimelineEvent, PrintDocument } from '../features/orders/model/types'
import type { Movement } from '../features/inventory/model/types'
import { cents, decimal, money, dateTime, dayBoundary, addDays } from '../shared/commerce'
import { downloadCsv } from '../shared/api/download'
import { fetchOrderableProducts } from '../features/products/api/productsApi'
import type { Product, ProductSKU } from '../features/products/model/types'
import { fetchProductDetail } from '../features/products/api/productDetailsApi'
import { fetchPrintDocument } from '../features/orders/api/printingApi'
import { logout } from '../features/auth/api/authApi'
import { useI18n } from '../shared/i18n'

const props = defineProps<{ username: string }>()
const { t } = useI18n()
const initialParams = new URLSearchParams(window.location?.search ?? '')
const filter = ref<OrderFilterStatus | 'all'>(
  (initialParams.get('status') as OrderFilterStatus) || 'all',
)
const filters = ref<OrderFilters>({})
const keywordDraft = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const exporting = ref(false)
const detailOrderId = ref<number | null>(null)
const correctionTarget = ref<TimelineEvent | null>(null)
const correctionContent = ref('')
const correctionReason = ref('')
const presetName = ref('')
const presets = ref<{ name: string; params: string }[]>([])
const activeConditions = computed(() =>
  [
    filters.value.keyword && `关键词：${filters.value.keyword}`,
    filter.value !== 'all' && `状态：${statusLabel.value[filter.value]}`,
    filters.value.statuses?.length &&
      `多状态：${filters.value.statuses.map((value) => statusLabel.value[value]).join('、')}`,
    `${timeFieldLabel.value}${filters.value.unknown_time ? '待核实' : ''}`,
    filters.value.start_at && `起：${dateTime(filters.value.start_at)}`,
    filters.value.end_at && `止（不含）：${dateTime(filters.value.end_at)}`,
    filters.value.min_total && `最低应收：${money(filters.value.min_total)}`,
    filters.value.max_total && `最高应收：${money(filters.value.max_total)}`,
    filters.value.print_request_status &&
      `打印：${({ never: '从未请求', requested: '已请求', multiple: '多次请求' } as Record<string, string>)[filters.value.print_request_status]}`,
    filters.value.overdue && '收款满 24 小时未发货',
  ].filter(Boolean),
)
const statusCounts = ref<Partial<Record<OrderFilterStatus, number>>>({})
const orders = ref<OrderRecord[]>([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const selectedOrder = ref<OrderRecord | null>(null)
const loading = ref(true)
const lastLoadedAt = ref('')
const error = ref('')
const saving = ref(false)
const updatingStatusId = ref<number | null>(null)
const dialogOpen = ref(false)
const dialogLoading = ref(false)
const dialogError = ref('')
const customers = ref<CustomerRecord[]>([])
const addresses = ref<AddressRecord[]>([])
const products = ref<Product[]>([])
const selectedCustomerId = ref<number | null>(null)
const selectedAddressId = ref<number | null>(null)
type OrderLineDraft = { product_id: number | null; sku_id?: number | null; quantity: number }
const skuOptions = ref<Record<number, ProductSKU[]>>({})
const loadingSKUs = ref<number[]>([])
const skuError = ref('')
const lineItems = ref<OrderLineDraft[]>([{ product_id: null, quantity: 1 }])
const shippingFee = ref('0.00')
const note = ref('')
let ordersRequestId = 0
// 列表、详情、地址和留痕各有请求序号；旧响应晚到时，不能覆盖用户刚切换的选择。
let detailRequestId = 0
let addressRequestId = 0
let evidenceRequestId = 0
let creationKey = ''
let restoring = false
const actionKeys = new Map<string, string>()
// 以下三组分页分别属于时间线、库存流水和打印文档，不能共用页码。
const events = ref<TimelineEvent[]>([])
const eventPage = ref(1)
const eventTotal = ref(0)
const detailMovements = ref<Movement[]>([])
const movementPage = ref(1)
const movementTotal = ref(0)
const documents = ref<PrintDocument[]>([])
const documentPage = ref(1)
const documentTotal = ref(0)
const detailError = ref('')
const detailLoading = ref(false)
const internalNote = ref('')
const selectedIds = ref<number[]>([])
const selectionNotice = ref('')
const printOpen = ref(false)
const printOrders = ref<OrderRecord[]>([])
const printSources = ref<Record<number, number>>({})
const printReason = ref('')
const pickingNote = ref('')
const includeAmount = ref(false)
const printError = ref('')
const printSaving = ref(false)
const printLayout = ref<'orders' | 'summary'>('orders')
let printKey = ''
let printSignature = ''
const timeLabels = { created_at: '创建时间', paid_at: '付款时间', completed_at: '完成时间' }
const timeFieldLabel = computed(() => timeLabels[filters.value.time_field ?? 'created_at'])
const reprintRequired = computed(() =>
  printOrders.value.some((order) => order.print_request_count > 0),
)

const statusOptions: OrderFilterStatus[] = [
  'pending_payment',
  'paid_pending_shipment',
  'in_delivery',
  'completed',
  'cancelled',
  'refunded',
  'aftersale_pending',
]
const statusLabel = computed(() => ({
  pending_payment: t('Pending payment'),
  paid_pending_shipment: t('Paid / pending shipment'),
  in_delivery: t('In delivery'),
  completed: t('Completed'),
  cancelled: t('Cancelled'),
  refunded: t('Refunded'),
  aftersale_pending: '售后处理中',
}))
const productsById = computed(() => new Map(products.value.map((product) => [product.id, product])))
let formGeneration = 0
async function chooseLineProduct(line: OrderLineDraft, preferredSku?: number) {
  const productId = line.product_id
  line.sku_id = null
  if (productId == null) return
  const generation = formGeneration
  loadingSKUs.value.push(productId)
  skuError.value = ''
  try {
    const value = await fetchProductDetail(productId)
    if (generation !== formGeneration) return
    skuOptions.value[productId] = value.skus
    if (line.product_id !== productId) return
    const active = value.skus.filter((sku) => sku.active)
    line.sku_id =
      active.find((sku) => sku.id === preferredSku)?.id ??
      (active.length === 1 ? active[0]!.id : null)
  } catch (reason) {
    if (generation === formGeneration && line.product_id === productId) {
      skuOptions.value[productId] = []
      skuError.value = reason instanceof Error ? reason.message : 'SKU 加载失败，请重新选择商品'
    }
  } finally {
    if (generation === formGeneration) {
      const index = loadingSKUs.value.indexOf(productId)
      if (index >= 0) loadingSKUs.value.splice(index, 1)
    }
  }
}
const subtotalCents = computed(() =>
  lineItems.value.reduce((sum, line) => {
    const product = line.product_id == null ? undefined : productsById.value.get(line.product_id)
    return (
      sum +
      cents(
        (line.product_id == null
          ? undefined
          : skuOptions.value[line.product_id]?.find((sku) => sku.id === line.sku_id)?.price) ??
          product?.price ??
          0,
      ) *
        BigInt(Number.isInteger(line.quantity) ? line.quantity : 0)
    )
  }, 0n),
)
const subtotal = computed(() => decimal(subtotalCents.value))
const formTotal = computed(() => decimal(subtotalCents.value + cents(shippingFee.value)))
const selectedAddress = computed(
  () => addresses.value.find((address) => address.id === selectedAddressId.value) ?? null,
)
const canSubmit = computed(() => {
  if (
    saving.value ||
    loadingSKUs.value.length > 0 ||
    dialogLoading.value ||
    selectedCustomerId.value == null ||
    selectedAddressId.value == null ||
    lineItems.value.length === 0
  )
    return false
  if (selectedAddress.value?.customer_id !== selectedCustomerId.value) return false
  const requested = new Map<string, { quantity: number; stock: number }>()
  for (const line of lineItems.value) {
    if (
      line.product_id == null ||
      !Number.isInteger(line.quantity) ||
      line.quantity < 1 ||
      line.quantity > 10000
    )
      return false
    const choices = skuOptions.value[line.product_id]
    const sku = choices?.find((row) => row.id === line.sku_id && row.active)
    if (choices && !sku) return false
    const key = sku ? `sku:${sku.id}` : `product:${line.product_id}`
    requested.set(key, {
      quantity: (requested.get(key)?.quantity ?? 0) + line.quantity,
      stock: sku?.stock ?? productsById.value.get(line.product_id)?.stock ?? 0,
    })
  }
  return (
    [...requested].every(([, value]) => value.stock >= value.quantity) &&
    /^\d+(\.\d{1,2})?$/.test(shippingFee.value)
  )
})

async function loadOrders() {
  const requestId = ++ordersRequestId
  detailRequestId++
  loading.value = true
  error.value = ''
  selectedOrder.value = null
  if (selectedIds.value.length) selectionNotice.value = '筛选或页码已变化，已清空当前页选择。'
  selectedIds.value = []
  writeUrl()
  try {
    const result = await fetchOrders(
      filter.value === 'all' ? undefined : filter.value,
      page.value,
      pageSize.value,
      filters.value,
    )
    if (requestId !== ordersRequestId) return
    total.value = result.total
    // 状态变更可能清空最后一页，此时退回仍有结果的最后一页。
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await loadOrders()
      return
    }
    orders.value = result.items
    lastLoadedAt.value = new Date().toISOString()
    statusCounts.value = result.status_counts ?? {}
    const target = orders.value.find((row) => row.id === detailOrderId.value)
    if (target) await openOrder(target, false)
    else if (detailOrderId.value) {
      detailOrderId.value = null
      writeUrl()
    }
  } catch (reason) {
    if (requestId !== ordersRequestId) return
    error.value = reason instanceof Error ? reason.message : t('Could not load orders')
  } finally {
    if (requestId === ordersRequestId) loading.value = false
  }
}

function changePage(nextPage: number) {
  page.value = Math.min(Math.max(nextPage, 1), pageCount.value)
  detailOrderId.value = null
  writeUrl(true)
  return loadOrders()
}

async function openOrder(order: OrderRecord, navigate = true) {
  if (!orders.value.some((row) => row.id === order.id)) return
  detailOrderId.value = order.id
  if (navigate) writeUrl(true)
  const requestId = ++detailRequestId
  selectedOrder.value = null
  detailLoading.value = true
  detailError.value = ''
  events.value = []
  detailMovements.value = []
  documents.value = []
  internalNote.value = ''
  correctionTarget.value = null
  eventPage.value = 1
  movementPage.value = 1
  documentPage.value = 1
  try {
    const detail = await fetchOrder(order.id)
    if (requestId !== detailRequestId) return
    selectedOrder.value = detail
    await loadEvidence(requestId)
  } catch (reason) {
    if (requestId !== detailRequestId) return
    detailError.value = reason instanceof Error ? reason.message : t('Could not load order details')
  } finally {
    if (requestId === detailRequestId) detailLoading.value = false
  }
}

function retryOrderDetails() {
  if (detailLoading.value) return
  if (selectedOrder.value) return loadEvidence()
  const order = orders.value.find((row) => row.id === detailOrderId.value)
  if (order) return openOrder(order, false)
}

// 售后写入后重读当前查询，统一刷新归属、数量和留痕。
// 例如退款完成会离开“售后处理中”，但仍保留原来的履约状态。
async function refreshAfterSale() {
  if (!selectedOrder.value) return
  detailOrderId.value = selectedOrder.value.id
  await loadOrders()
}

function closeOrder() {
  if (saving.value || updatingStatusId.value != null) return
  const id = detailOrderId.value
  detailRequestId++
  evidenceRequestId++
  selectedOrder.value = null
  detailOrderId.value = null
  detailLoading.value = false
  detailError.value = ''
  correctionTarget.value = null
  writeUrl(true)
  if (id)
    void nextTick(() =>
      document.getElementById?.(`order-${id}`)?.scrollIntoView({ block: 'nearest' }),
    )
}
function orderLink(id: number) {
  const params = new URLSearchParams(window.location.search)
  params.set('order_id', String(id))
  return `/orders?${params}`
}
function clickOrder(event: MouseEvent, order: OrderRecord) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
  event.preventDefault()
  if (loading.value || saving.value || updatingStatusId.value != null) return
  if (detailOrderId.value === order.id) closeOrder()
  else return openOrder(order)
}

async function loadEvidence(id = detailRequestId) {
  if (!selectedOrder.value) return
  const orderId = selectedOrder.value.id
  const evidenceId = ++evidenceRequestId
  detailError.value = ''
  try {
    const [timeline, ledger, docs] = await Promise.all([
      request<Page<TimelineEvent>>(
        `/order-operations/orders/${orderId}/events?page=${eventPage.value}`,
      ),
      request<Page<Movement>>(
        `/inventory/movements?order_id=${orderId}&page=${movementPage.value}`,
      ),
      request<Page<PrintDocument>>(
        `/order-operations/orders/${orderId}/documents?page=${documentPage.value}`,
      ),
    ])
    if (
      id !== detailRequestId ||
      evidenceId !== evidenceRequestId ||
      selectedOrder.value?.id !== orderId
    )
      return
    events.value = timeline.items
    eventTotal.value = timeline.total
    detailMovements.value = ledger.items
    movementTotal.value = ledger.total
    documents.value = docs.items
    documentTotal.value = docs.total
  } catch (reason) {
    if (id === detailRequestId && evidenceId === evidenceRequestId)
      detailError.value = reason instanceof Error ? reason.message : '操作依据加载失败'
  }
}

function writeUrl(push = false) {
  const params = new URLSearchParams({
    page: String(page.value),
    page_size: String(pageSize.value),
  })
  if (filter.value !== 'all') params.set('status', filter.value)
  if (detailOrderId.value) params.set('order_id', String(detailOrderId.value))
  Object.entries(filters.value).forEach(([key, value]) => {
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item))
    else if (value !== undefined && value !== '' && value !== false) params.set(key, String(value))
  })
  const path = `/orders?${params}`
  if (push) window.history.pushState(null, '', path)
  else window.history.replaceState(null, '', path)
}

function restoreFilters() {
  restoring = true
  const params = new URLSearchParams(window.location?.search ?? '')
  filter.value = (params.get('status') as OrderFilterStatus) || 'all'
  page.value = Math.max(1, Number(params.get('page')) || 1)
  pageSize.value = params.get('page_size') === '50' ? 50 : 20
  detailOrderId.value = Number(params.get('order_id')) || null
  filters.value = {
    keyword: params.get('keyword') || '',
    statuses: params.getAll('statuses') as OrderStatus[],
    time_field: (params.get('time_field') as OrderFilters['time_field']) || 'created_at',
    start_at: params.get('start_at') || '',
    end_at: params.get('end_at') || '',
    as_of: params.get('as_of') || '',
    min_total: params.get('min_total') || '',
    max_total: params.get('max_total') || '',
    print_request_status: params.get('print_request_status') || '',
    overdue: params.get('overdue') === 'true',
    unknown_time: params.get('unknown_time') === 'true',
    sort: params.get('sort') === 'created_asc' ? 'created_asc' : 'created_desc',
  }
  keywordDraft.value = filters.value.keyword || ''
  restoring = false
  void loadOrders()
}

function applyFilters() {
  if (!validateFilters()) return
  filters.value.keyword = keywordDraft.value.trim()
  page.value = 1
  detailOrderId.value = null
  writeUrl(true)
  void loadOrders()
}
function clearFilters() {
  restoring = true
  filters.value = { statuses: [], time_field: 'created_at' }
  keywordDraft.value = ''
  filter.value = 'all'
  detailOrderId.value = null
  page.value = 1
  restoring = false
  writeUrl(true)
  void loadOrders()
}

function switchToLive() {
  filters.value.as_of = ''
  applyFilters()
}

function changePageSize() {
  page.value = 1
  detailOrderId.value = null
  writeUrl(true)
  void loadOrders()
}

function validateFilters() {
  if (
    filters.value.start_at &&
    filters.value.end_at &&
    new Date(filters.value.start_at) >= new Date(filters.value.end_at)
  ) {
    error.value = '开始时间必须早于截止时间'
    return false
  }
  for (const [field, label] of [
    ['min_total', '最低应收'],
    ['max_total', '最高应收'],
  ] as const) {
    const value = filters.value[field]
    if (value && (!/^\d+(\.\d{1,2})?$/.test(value) || cents(value) > 999999999999n)) {
      error.value = `${label}须为 0–9999999999.99 元，最多两位小数`
      return false
    }
  }
  if (
    filters.value.min_total &&
    filters.value.max_total &&
    cents(filters.value.min_total) > cents(filters.value.max_total)
  ) {
    error.value = '最低应收不能高于最高应收'
    return false
  }
  return true
}
function recentThirtyDays() {
  const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
  filters.value.start_at = dayBoundary(addDays(today, -29))
  filters.value.end_at = dayBoundary(addDays(today, 1))
  filters.value.unknown_time = false
  filters.value.as_of = ''
  applyFilters()
}
function changeUnknownTime() {
  if (filters.value.unknown_time) {
    filters.value.start_at = ''
    filters.value.end_at = ''
    filters.value.as_of = ''
  }
}
async function exportOrders() {
  if (exporting.value || !validateFilters()) return
  exporting.value = true
  error.value = ''
  try {
    const params = new URLSearchParams(window.location.search)
    for (const field of ['page', 'page_size', 'order_id']) params.delete(field)
    await downloadCsv(`/orders/export?${params}`, '订单明细.csv')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '订单导出失败'
  } finally {
    exporting.value = false
  }
}
function savePreset() {
  if (!presetName.value.trim() || !validateFilters()) return
  const params = new URLSearchParams()
  if (filter.value !== 'all') params.set('status', filter.value)
  Object.entries({ ...filters.value, keyword: keywordDraft.value.trim() }).forEach(
    ([key, value]) => {
      if (key === 'as_of') return
      if (Array.isArray(value)) value.forEach((item) => params.append(key, item))
      else if (value !== undefined && value !== '' && value !== false)
        params.set(key, String(value))
    },
  )
  const name = presetName.value.trim().slice(0, 40)
  const next = [
    ...presets.value.filter((item) => item.name !== name),
    { name, params: String(params) },
  ].slice(-20)
  try {
    window.localStorage.setItem(`orders:filters:${props.username}`, JSON.stringify(next))
    presets.value = next
    presetName.value = ''
  } catch {
    error.value = '浏览器未允许保存常用筛选'
  }
}
function usePreset(params: string) {
  window.history.pushState(null, '', `/orders?${params}`)
  restoreFilters()
}
function removePreset(name: string) {
  try {
    const next = presets.value.filter((item) => item.name !== name)
    window.localStorage.setItem(`orders:filters:${props.username}`, JSON.stringify(next))
    presets.value = next
  } catch {
    error.value = '浏览器未允许更新常用筛选'
  }
}
function shortcuts(event: KeyboardEvent) {
  if (event.ctrlKey || event.metaKey || event.altKey) return
  const target = event.target as HTMLElement | null
  if (
    event.key !== 'Escape' &&
    (target?.matches?.('input, textarea, select') || target?.isContentEditable)
  )
    return
  if (event.key === '/' && !dialogOpen.value && !printOpen.value && !correctionTarget.value) {
    event.preventDefault()
    searchInput.value?.focus()
  } else if (event.key === 'Escape') {
    if (saving.value || printSaving.value || updatingStatusId.value != null) return
    if (correctionTarget.value) correctionTarget.value = null
    else if (printOpen.value) printOpen.value = false
    else if (dialogOpen.value) closeDialog()
    else if (selectedOrder.value || detailLoading.value) closeOrder()
  }
}

function changeEvidencePage(kind: 'event' | 'movement' | 'document', delta: number) {
  if (kind === 'event') eventPage.value += delta
  else if (kind === 'movement') movementPage.value += delta
  else documentPage.value += delta
  void loadEvidence()
}
function localInput(value?: string) {
  if (!value) return ''
  const timestamp = new Date(value).getTime()
  return Number.isFinite(timestamp)
    ? new Date(timestamp + 8 * 3600000).toISOString().slice(0, 16)
    : ''
}
function updateTime(field: 'start_at' | 'end_at', event: Event) {
  const value = (event.target as HTMLInputElement).value
  filters.value[field] = value ? `${value}:00+08:00` : ''
  filters.value.as_of = ''
  if (value) filters.value.unknown_time = false
}
function toggleSelection(id: number) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((value) => value !== id)
    : [...selectedIds.value, id].slice(0, 50)
  selectionNotice.value = ''
}
function keyFor(action: string) {
  // 网络失败重试保留原键；成功后的新操作由调用方删键，防止重复写备注或扣库存。
  if (!actionKeys.has(action)) actionKeys.set(action, crypto.randomUUID())
  return actionKeys.get(action)!
}
async function appendNote() {
  if (saving.value || !selectedOrder.value || !internalNote.value.trim()) return
  const orderId = selectedOrder.value.id
  const requestId = detailRequestId
  const content = internalNote.value.trim()
  const action = `note:${orderId}:${content}`
  saving.value = true
  detailError.value = ''
  try {
    await request(`/order-operations/orders/${orderId}/notes`, 'POST', {
      content,
      idempotency_key: keyFor(action),
    })
    actionKeys.delete(action)
    if (requestId !== detailRequestId || selectedOrder.value?.id !== orderId) return
    internalNote.value = ''
    eventPage.value = 1
    await loadEvidence()
  } catch (reason) {
    if (requestId === detailRequestId)
      detailError.value = reason instanceof Error ? reason.message : '追加备注失败'
  } finally {
    saving.value = false
  }
}
function openCorrection(event: TimelineEvent) {
  correctionTarget.value = event
  correctionContent.value = ''
  correctionReason.value = ''
}
async function saveCorrection() {
  if (
    saving.value ||
    !selectedOrder.value ||
    !correctionTarget.value ||
    !correctionContent.value.trim() ||
    !correctionReason.value.trim()
  )
    return
  const orderId = selectedOrder.value.id
  const requestId = detailRequestId
  const action = `correction:${orderId}:${correctionTarget.value.id}:${correctionContent.value.trim()}:${correctionReason.value.trim()}`
  saving.value = true
  detailError.value = ''
  try {
    await request(
      `/order-operations/orders/${orderId}/events/${correctionTarget.value.id}/corrections`,
      'POST',
      {
        content: correctionContent.value.trim(),
        reason: correctionReason.value.trim(),
        idempotency_key: keyFor(action),
      },
    )
    actionKeys.delete(action)
    if (requestId !== detailRequestId) return
    correctionTarget.value = null
    eventPage.value = 1
    await loadEvidence()
  } catch (reason) {
    if (requestId === detailRequestId)
      detailError.value = reason instanceof Error ? reason.message : '更正记录追加失败'
  } finally {
    saving.value = false
  }
}
async function advanceOrder(order: OrderRecord) {
  if (updatingStatusId.value != null || order.status !== 'in_delivery') return
  const next = 'completed'
  if (!window.confirm('人工确认此订单已完成？此操作不代表已收到快递签收回执。')) return
  updatingStatusId.value = order.id
  error.value = ''
  try {
    await updateOrderStatus(order.id, next, undefined, keyFor(`${order.id}:${next}`))
    await loadOrders()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '订单状态更新失败'
  } finally {
    updatingStatusId.value = null
  }
}
function openPrint(rows: OrderRecord[], source?: PrintDocument) {
  if (printSaving.value || !rows.length || rows.length > 50) return
  printOrders.value = rows
  printSources.value = source ? { [source.order_id]: source.id } : {}
  printReason.value = ''
  pickingNote.value = ''
  includeAmount.value = false
  printLayout.value = 'orders'
  printError.value = ''
  printKey = crypto.randomUUID()
  printSignature = ''
  printOpen.value = true
}
async function submitPrint() {
  // 先在点击事件中打开窗口，避免等待接口后再打开被浏览器当作弹窗拦截。
  if (printSaving.value || (reprintRequired.value && !printReason.value.trim())) return
  const popup = window.open('', '_blank')
  if (!popup) {
    printError.value = '请允许弹出窗口后重试'
    return
  }
  popup.document.body.textContent = '正在生成配货文档…'
  printSaving.value = true
  printError.value = ''
  const layout = printLayout.value
  const payload = {
    order_ids: printOrders.value.map((order) => order.id),
    source_documents: printSources.value,
    include_amount: layout === 'orders' && includeAmount.value,
    picking_note: pickingNote.value,
    reprint_reason: printReason.value.trim(),
    layout,
  }
  const signature = JSON.stringify(payload)
  if (printSignature && printSignature !== signature) printKey = crypto.randomUUID()
  printSignature = signature
  try {
    const job = await request<{ id: number }>('/order-operations/print-jobs', 'POST', {
      ...payload,
      idempotency_key: printKey,
    })
    const html = await fetchPrintDocument(job.id, layout)
    if (popup.closed)
      throw new Error('打印请求已登记，但文档窗口已关闭。可从订单详情查看历史文档。')
    popup.document.open()
    popup.document.write(html)
    popup.document.close()
    popup.focus()
    // 浏览器无法证明纸张实际打印成功，因此后台只记录“明确发起过打印请求”。
    await (
      popup as Window & { printRegisteredDocument: () => Promise<void> }
    ).printRegisteredDocument()
    printOpen.value = false
    await loadOrders()
  } catch (reason) {
    if (!popup.closed) popup.close()
    printError.value = reason instanceof Error ? reason.message : '打印请求失败'
  } finally {
    printSaving.value = false
  }
}

async function loadAddresses(customerId: number | null) {
  const requestId = ++addressRequestId
  addresses.value = []
  selectedAddressId.value = null
  if (customerId == null) return
  try {
    const rows = await fetchCustomerAddresses(customerId)
    if (requestId !== addressRequestId || selectedCustomerId.value !== customerId) return
    addresses.value = rows
    const defaultAddress =
      addresses.value.find((address) => address.is_default) ?? addresses.value[0]
    selectedAddressId.value = defaultAddress?.id ?? null
    if (rows.length === 0) dialogError.value = t('Selected customer has no saved address.')
  } catch (reason) {
    if (requestId !== addressRequestId || selectedCustomerId.value !== customerId) return
    dialogError.value = reason instanceof Error ? reason.message : t('Could not load addresses')
  }
}

async function changeCustomer(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedCustomerId.value = value ? Number(value) : null
  dialogError.value = ''
  await loadAddresses(selectedCustomerId.value)
}

async function openCreateDialog() {
  const generation = ++formGeneration
  skuOptions.value = {}
  loadingSKUs.value = []
  skuError.value = ''
  addressRequestId++
  dialogOpen.value = true
  dialogLoading.value = true
  dialogError.value = ''
  customers.value = []
  addresses.value = []
  products.value = []
  selectedCustomerId.value = null
  selectedAddressId.value = null
  lineItems.value = [{ product_id: null, quantity: 1 }]
  shippingFee.value = '0.00'
  creationKey = crypto.randomUUID()
  note.value = ''
  try {
    const [customerRows, productRows] = await Promise.all([
      fetchCustomers(),
      fetchOrderableProducts(),
    ])
    if (generation !== formGeneration) return
    customers.value = customerRows
    products.value = productRows
    selectedCustomerId.value = null
    if (customerRows.length === 0)
      dialogError.value = t('Create a customer and address before adding an order.')
    else if (productRows.length === 0) dialogError.value = t('No products with available stock.')
  } catch (reason) {
    if (generation === formGeneration)
      dialogError.value =
        reason instanceof Error ? reason.message : t('Could not load order form data')
  } finally {
    if (generation === formGeneration) dialogLoading.value = false
  }
}

function closeDialog() {
  if (saving.value) return
  addressRequestId++
  formGeneration++
  loadingSKUs.value = []
  dialogOpen.value = false
  dialogError.value = ''
}

function addLineItem() {
  lineItems.value.push({ product_id: null, quantity: 1 })
}

function removeLineItem(index: number) {
  if (lineItems.value.length > 1) lineItems.value.splice(index, 1)
}

async function saveOrder() {
  if (!canSubmit.value || selectedCustomerId.value == null || selectedAddress.value == null) return
  saving.value = true
  dialogError.value = ''
  try {
    const created = await createOrder(
      {
        customer_id: selectedCustomerId.value,
        address_id: selectedAddress.value.id,
        items: lineItems.value.map((line) => ({
          product_id: line.product_id as number,
          ...(line.sku_id != null ? { sku_id: line.sku_id } : {}),
          quantity: line.quantity,
        })),
        shipping_fee: shippingFee.value,
        note: note.value.trim() || null,
      },
      creationKey,
    )
    dialogOpen.value = false
    page.value = 1
    await loadOrders()
    if (filter.value === 'all' || filter.value === created.status) await openOrder(created)
  } catch (reason) {
    dialogError.value = reason instanceof Error ? reason.message : t('Could not create order')
  } finally {
    saving.value = false
  }
}

async function cancelOrder(order: OrderRecord) {
  if (updatingStatusId.value != null) return
  const reason = window.prompt('请输入取消原因（必填）。已收款事实会保留，退款需另行核实。')?.trim()
  if (!reason) return
  updatingStatusId.value = order.id
  error.value = ''
  try {
    await updateOrderStatus(
      order.id,
      'cancelled',
      reason,
      keyFor(`${order.id}:cancelled:${reason}`),
    )
    await loadOrders()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not cancel order')
  } finally {
    updatingStatusId.value = null
  }
}

async function confirmPayment(order: OrderRecord) {
  if (updatingStatusId.value != null) return
  if (!window.confirm(t('Confirm payment status confirmation'))) return
  updatingStatusId.value = order.id
  error.value = ''
  try {
    await updateOrderStatus(
      order.id,
      'paid_pending_shipment',
      undefined,
      keyFor(`${order.id}:paid`),
    )
    await loadOrders()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not update order status')
  } finally {
    updatingStatusId.value = null
  }
}

watch(
  filter,
  () => {
    if (restoring) return
    filters.value.statuses = []
    page.value = 1
    detailOrderId.value = null
    writeUrl(true)
    loadOrders()
  },
  { flush: 'sync' },
)
onMounted(() => {
  restoreFilters()
  const presetProduct = Number(initialParams.get('create_product_id'))
  const presetCustomer = Number(initialParams.get('create_customer_id'))
  // 客户详情仅携带客户 ID；创建时仍重新读取客户、地址和商品，服务端再次校验归属与库存。
  if (
    !(Number.isSafeInteger(presetProduct) && presetProduct > 0) &&
    Number.isSafeInteger(presetCustomer) &&
    presetCustomer > 0
  ) {
    void openCreateDialog().then(async () => {
      if (!dialogOpen.value) return
      if (!customers.value.some((customer) => customer.id === presetCustomer)) {
        dialogError.value = '所选客户不存在，请重新选择客户'
        return
      }
      selectedCustomerId.value = presetCustomer
      await loadAddresses(presetCustomer)
    })
  }
  if (Number.isSafeInteger(presetProduct) && presetProduct > 0) {
    void openCreateDialog().then(async () => {
      if (!dialogOpen.value) return
      const line = lineItems.value[0]!
      line.product_id = products.value.some((product) => product.id === presetProduct)
        ? presetProduct
        : null
      line.quantity = Math.min(
        10000,
        Math.max(1, Number(initialParams.get('create_quantity')) || 1),
      )
      if (line.product_id != null)
        await chooseLineProduct(line, Number(initialParams.get('create_sku_id')))
      else skuError.value = '所选商品当前不可下单，请刷新商品库存后重试'
    })
  }
  window.addEventListener('popstate', restoreFilters)
  window.addEventListener('keydown', shortcuts)
  try {
    const stored = JSON.parse(
      window.localStorage?.getItem(`orders:filters:${props.username}`) || '[]',
    )
    if (Array.isArray(stored))
      presets.value = stored
        .filter((item) => typeof item.name === 'string' && typeof item.params === 'string')
        .slice(-20)
  } catch {
    /* A corrupt or blocked local preference does not prevent loading orders. */
  }
})
onBeforeUnmount(() => {
  formGeneration++
  ordersRequestId++
  detailRequestId++
  addressRequestId++
  evidenceRequestId++
  window.removeEventListener('popstate', restoreFilters)
  window.removeEventListener('keydown', shortcuts)
})

async function signOut() {
  try {
    await logout()
    window.location.assign('/login')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Sign out failed')
  }
}
</script>

<template>
  <main class="app-frame">
    <AppSidebar active-page="orders" />
    <section class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          <span>{{ t('Corelystics Store') }}</span
          ><ChevronRight :size="15" /><strong><Package :size="15" /> {{ t('Orders') }}</strong>
        </div>
        <div class="shipping-topbar-spacer"></div>
        <div class="top-actions">
          <LanguageSwitcher /><span class="signed-in-user" :title="props.username">{{
            props.username
          }}</span
          ><button class="icon-button" :aria-label="t('Sign out')" @click="signOut">
            <LogOut :size="18" />
          </button>
        </div>
      </header>
      <section class="shipping-page-content">
        <div class="shipping-heading">
          <div>
            <p class="shipping-eyebrow">ORDER WORKBENCH</p>
            <h1>订单工作台</h1>
            <p>查询、核对和配货，每一步都有依据。</p>
          </div>
          <div class="section-actions">
            <button class="ops-button" :disabled="loading" @click="loadOrders">刷新</button>
            <button class="ops-button" :disabled="loading || exporting" @click="exportOrders">
              {{ exporting ? '正在导出…' : '导出 CSV' }}
            </button>
            <label class="order-filter"
              ><span>{{ t('Status') }}</span
              ><select v-model="filter">
                <option value="all">{{ t('All orders') }}</option>
                <option v-for="value in statusOptions" :key="value" :value="value">
                  {{ statusLabel[value] }}
                </option>
              </select></label
            >
            <button class="add-product-button" @click="openCreateDialog">
              <Plus :size="15" /> {{ t('Create order') }}
            </button>
          </div>
        </div>
        <div class="ops-tabs order-status-tabs">
          <button :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button
          ><button
            v-for="value in statusOptions"
            :key="value"
            :class="{ active: filter === value }"
            @click="filter = value"
          >
            {{ statusLabel[value] }} <span>{{ statusCounts[value] ?? 0 }}</span>
          </button>
        </div>
        <p v-if="filter === 'refunded' || filter === 'aftersale_pending'" class="ops-subtle">
          {{
            filter === 'refunded'
              ? '包含已登记实际退款（部分或全额）和历史已退款订单；履约状态分别保留。'
              : '已提交申请但尚未结束的售后；申请不等于实际退款。'
          }}
        </p>
        <form class="ops-order-filters" @submit.prevent="applyFilters">
          <div class="ops-filterbar">
            <label class="grow"
              >关键词<input
                ref="searchInput"
                v-model="keywordDraft"
                placeholder="订单号、完整电话、收件人、商品快照名称" /></label
            ><label
              >时间口径<select v-model="filters.time_field" @change="filters.as_of = ''">
                <option value="created_at">创建时间</option>
                <option value="paid_at">付款时间</option>
                <option value="completed_at">完成时间</option>
              </select></label
            ><label
              >开始时间（上海）<input
                type="datetime-local"
                :value="localInput(filters.start_at)"
                @change="updateTime('start_at', $event)" /></label
            ><label
              >截止时间（不含）<input
                type="datetime-local"
                :value="localInput(filters.end_at)"
                @change="updateTime('end_at', $event)" /></label
            ><button class="ops-button primary">查询</button
            ><button class="ops-button" type="button" @click="recentThirtyDays">近 30 天</button
            ><button class="ops-button" type="button" @click="clearFilters">清空</button>
          </div>
          <details class="ops-advanced">
            <summary>高级筛选 · 金额 / 多状态 / 打印请求</summary>
            <div class="ops-filterbar">
              <label
                >最低应收 / 元<input
                  v-model="filters.min_total"
                  type="number"
                  min="0"
                  step="0.01" /></label
              ><label
                >最高应收 / 元<input
                  v-model="filters.max_total"
                  type="number"
                  min="0"
                  step="0.01" /></label
              ><label
                >打印请求<select v-model="filters.print_request_status">
                  <option value="">全部</option>
                  <option value="never">从未请求打印</option>
                  <option value="requested">已请求打印</option>
                  <option value="multiple">多次请求打印</option>
                </select></label
              ><label class="ops-inline-field"
                ><input v-model="filters.overdue" type="checkbox" /> 收款满 24 小时未发货</label
              ><label class="ops-inline-field"
                ><input
                  v-model="filters.unknown_time"
                  type="checkbox"
                  @change="changeUnknownTime"
                />
                {{ timeFieldLabel }}待核实</label
              >
            </div>
            <div class="ops-filterbar">
              <span>多状态（先选择“全部”）</span
              ><label v-for="value in statusOptions" :key="value" class="ops-inline-field"
                ><input
                  v-model="filters.statuses"
                  :value="value"
                  type="checkbox"
                  :disabled="filter !== 'all'"
                />{{ statusLabel[value] }}</label
              >
            </div>
          </details>
        </form>
        <details class="ops-advanced order-presets">
          <summary>常用筛选（本浏览器） · 快捷键 / 搜索，Esc 关闭详情</summary>
          <div class="ops-filterbar">
            <label
              >筛选名称<input
                v-model="presetName"
                maxlength="40"
                placeholder="例如：待发货且从未请求打印"
            /></label>
            <button class="ops-button" :disabled="!presetName.trim()" @click="savePreset">
              保存此筛选
            </button>
          </div>
          <div class="ops-filterbar">
            <span v-if="!presets.length" class="ops-subtle"
              >尚无常用筛选；保存后不携带概览快照边界。</span
            >
            <span v-for="preset in presets" :key="preset.name" class="ops-inline-field">
              <button class="text-action" @click="usePreset(preset.params)">
                {{ preset.name }}
              </button>
              <button
                class="text-action"
                :aria-label="`删除筛选 ${preset.name}`"
                @click="removePreset(preset.name)"
              >
                ×
              </button>
            </span>
          </div>
        </details>
        <div class="ops-filterbar" aria-label="当前筛选条件">
          <span v-for="condition in activeConditions" :key="String(condition)" class="ops-badge">{{
            condition
          }}</span>
        </div>
        <div class="ops-meta">
          <span v-if="lastLoadedAt">列表更新于 {{ dateTime(lastLoadedAt) }}</span>
          <span
            >当前按<strong>{{ timeFieldLabel }}</strong
            >筛选 · 金额包含运费 · 未知时间不匹配日期条件</span
          ><span v-if="filters.as_of"
            >概览截至 {{ dateTime(filters.as_of) }}
            <button class="text-action" @click="switchToLive">切换为实时</button></span
          >
        </div>
        <p class="ops-subtle">
          修改筛选后点击查询。CSV
          导出已查询条件下的全部商品明细；同一订单的应收总额需按订单去重核对。
        </p>
        <div class="ops-selection">
          <label
            ><input
              type="checkbox"
              :checked="orders.length > 0 && selectedIds.length === orders.length"
              :disabled="loading"
              @change="
                selectedIds =
                  selectedIds.length === orders.length ? [] : orders.map((order) => order.id)
              "
            />
            选择当前页</label
          ><span>已选 {{ selectedIds.length }} 单</span
          ><button
            class="ops-button"
            :disabled="!selectedIds.length || loading"
            @click="openPrint(orders.filter((order) => selectedIds.includes(order.id)))"
          >
            请求批量打印配货单</button
          ><label class="ops-inline-field"
            >排序<select v-model="filters.sort" @change="applyFilters">
              <option value="created_desc">创建时间从新到旧</option>
              <option value="created_asc">创建时间从旧到新</option>
            </select></label
          ><label class="ops-inline-field"
            >每页<select v-model.number="pageSize" @change="changePageSize">
              <option :value="20">20</option>
              <option :value="50">50</option>
            </select></label
          >
        </div>
        <p v-if="selectionNotice" class="ops-subtle" role="status">{{ selectionNotice }}</p>
        <p v-if="detailError" class="ops-error" role="alert">
          {{ detailError }}
          <button class="text-action" :disabled="detailLoading" @click="retryOrderDetails">
            {{ selectedOrder ? '重试加载依据' : '重试加载详情' }}
          </button>
        </p>
        <p v-if="loading" class="module-empty">{{ t('Loading orders…') }}</p>
        <p v-if="error" class="auth-error" role="alert">
          {{ error }}<span v-if="orders.length"> · 保留上次成功加载的结果</span>
          <button class="text-action" @click="loadOrders">{{ t('Retry') }}</button>
        </p>
        <p v-if="!loading && !error && orders.length === 0" class="module-empty">
          {{ t('No orders found') }}
          <span> · {{ activeConditions.join('；') }}</span>
          <button class="text-action" @click="clearFilters">清空筛选</button>
        </p>
        <div v-if="orders.length" class="module-list" :aria-busy="loading">
          <article
            v-for="order in orders"
            :key="order.id"
            :id="`order-${order.id}`"
            class="module-card"
            :class="{ 'module-card-selected': detailOrderId === order.id }"
          >
            <label class="order-select"
              ><input
                type="checkbox"
                :aria-label="`选择 ${order.order_number}`"
                :checked="selectedIds.includes(order.id)"
                :disabled="loading"
                @change="toggleSelection(order.id)"
              />
              选择此单 ·
              {{
                order.print_request_count
                  ? `已请求打印 ${order.print_request_count} 次`
                  : '从未请求打印'
              }}</label
            >
            <small v-if="order.print_request_count > 1" class="ops-badge low_stock"
              >多次请求打印，请核对是否重复配货</small
            >
            <a
              class="module-card-main order-detail-link"
              :href="orderLink(order.id)"
              :aria-expanded="detailOrderId === order.id"
              :aria-controls="`order-detail-${order.id}`"
              :aria-disabled="loading || saving || updatingStatusId != null"
              @click="clickOrder($event, order)"
            >
              <span
                ><strong>{{ order.order_number }}</strong
                ><small>{{
                  order.created_time_verified ? dateTime(order.created_at) : '历史创建时间待核实'
                }}</small></span
              >
              <span
                >{{ statusLabel[order.status] }}
                <small v-if="order.aftersale_pending">售后处理中</small>
                <small v-if="Number(order.refunded_amount) > 0"
                  >已退款 {{ money(order.refunded_amount) }}</small
                >
              </span>
              <span>{{ order.recipient_name }} · {{ order.recipient_phone }}</span>
              <span class="order-summary-end">
                <span>{{ money(order.total) }}</span>
                <span class="order-toggle-hint">
                  {{ detailOrderId === order.id ? '收起详情' : '展开详情' }}
                  <ChevronRight
                    :size="16"
                    :class="{ 'order-toggle-expanded': detailOrderId === order.id }"
                    aria-hidden="true"
                  />
                </span>
              </span>
            </a>
            <div
              v-if="detailOrderId === order.id"
              :id="`order-detail-${order.id}`"
              class="order-detail"
              :aria-busy="detailLoading"
            >
              <p v-if="detailLoading && !selectedOrder" class="ops-subtle" role="status">
                正在加载订单详情…
              </p>
              <template v-if="selectedOrder?.id === order.id">
                <button
                  class="text-action"
                  :disabled="saving || updatingStatusId != null"
                  @click="closeOrder"
                >
                  收起详情 · 保留筛选与页码
                </button>
                <strong>{{ t('Shipping address') }}</strong>
                <p>
                  {{ selectedOrder.country }} {{ selectedOrder.province }}{{ selectedOrder.city
                  }}{{ selectedOrder.district }}{{ selectedOrder.address_detail }}
                </p>
                <strong>{{ t('Order items') }}</strong>
                <ul>
                  <li v-for="item in selectedOrder.items" :key="item.id">
                    #{{ item.product_id ?? '已删除' }} {{ item.product_name }}
                    {{ item.sku_label || '' }} × {{ item.quantity }} —
                    {{ money(item.line_total) }}
                  </li>
                </ul>
                <p>
                  商品小计 {{ money(selectedOrder.subtotal) }} · 运费
                  {{ money(selectedOrder.shipping_fee) }} · 应收 {{ money(selectedOrder.total) }}
                </p>
                <p>
                  客户 ID #{{ selectedOrder.customer_id }} · 收件人
                  {{ selectedOrder.recipient_name }} · {{ selectedOrder.recipient_phone }}
                </p>
                <div class="order-time-facts">
                  <p>
                    确认收款：{{
                      selectedOrder.paid_at
                        ? dateTime(selectedOrder.paid_at)
                        : selectedOrder.created_time_verified
                          ? '尚未确认收款'
                          : '时间待核实'
                    }}
                  </p>
                  <p>
                    交接出库：{{
                      selectedOrder.dispatch_started_at
                        ? dateTime(selectedOrder.dispatch_started_at)
                        : selectedOrder.created_time_verified
                          ? '尚未交接出库'
                          : '时间待核实'
                    }}
                  </p>
                  <p>
                    订单完成：{{
                      selectedOrder.completed_at
                        ? dateTime(selectedOrder.completed_at)
                        : selectedOrder.created_time_verified
                          ? '尚未完成'
                          : '时间待核实'
                    }}
                  </p>
                  <p>
                    取消：{{
                      selectedOrder.cancelled_at
                        ? dateTime(selectedOrder.cancelled_at)
                        : selectedOrder.created_time_verified
                          ? '未取消'
                          : '时间待核实'
                    }}
                  </p>
                </div>
                <AfterSalesPanel
                  :key="selectedOrder.id"
                  :order="selectedOrder"
                  @changed="refreshAfterSale"
                />
                <p v-if="selectedOrder.note">下单备注：{{ selectedOrder.note }}</p>
                <section class="order-shipment-summary">
                  <strong>快递发货</strong>
                  <p v-if="selectedOrder.shipment">
                    {{ selectedOrder.shipment.carrier_name }} ·
                    {{ selectedOrder.shipment.tracking_number }}
                  </p>
                  <p v-else class="ops-subtle">
                    {{
                      selectedOrder.status === 'paid_pending_shipment'
                        ? '待登记快递信息'
                        : '尚未登记快递信息'
                    }}
                  </p>
                  <p v-if="selectedOrder.shipment?.remark">
                    发货备注：{{ selectedOrder.shipment.remark }}
                  </p>
                  <a
                    v-if="
                      ['paid_pending_shipment', 'in_delivery', 'completed'].includes(
                        selectedOrder.status,
                      )
                    "
                    class="ops-button"
                    :href="`/shipping?status=${selectedOrder.status}&order_id=${selectedOrder.id}`"
                    >{{
                      selectedOrder.status === 'paid_pending_shipment'
                        ? '前往发货工作台'
                        : '查看 / 补录发货记录'
                    }}</a
                  >
                </section>
                <div class="order-detail-actions">
                  <button
                    v-if="selectedOrder.status === 'pending_payment'"
                    class="confirm-payment-button"
                    :disabled="updatingStatusId === order.id"
                    @click="confirmPayment(selectedOrder)"
                  >
                    {{ updatingStatusId === order.id ? t('Saving…') : t('Confirm payment') }}
                  </button>
                  <button
                    v-if="
                      selectedOrder.status === 'pending_payment' ||
                      selectedOrder.status === 'paid_pending_shipment'
                    "
                    class="delete-product-button"
                    :disabled="updatingStatusId === order.id"
                    @click="cancelOrder(selectedOrder)"
                  >
                    {{ updatingStatusId === order.id ? t('Saving…') : t('Cancel order') }}
                  </button>
                  <button
                    v-if="selectedOrder.status === 'in_delivery'"
                    class="ops-button primary"
                    :disabled="updatingStatusId === order.id"
                    @click="advanceOrder(selectedOrder)"
                  >
                    人工确认订单完成
                  </button>
                  <button
                    v-if="selectedOrder.status === 'paid_pending_shipment'"
                    class="ops-button"
                    @click="openPrint([selectedOrder])"
                  >
                    {{ selectedOrder.print_request_count ? '请求打印最新版本' : '请求打印配货单' }}
                  </button>
                </div>
                <div class="order-evidence-grid">
                  <section>
                    <h3>操作时间线</h3>
                    <p class="ops-subtle">手工收款仅表示后台登记；历史未知时间不推算。</p>
                    <ol class="order-timeline">
                      <li v-for="event in events" :key="event.id">
                        <span class="timeline-dot"></span><strong>{{ event.summary }}</strong>
                        <p>
                          {{ event.actor_name_snapshot }} ·
                          {{
                            event.occurred_at
                              ? dateTime(event.occurred_at)
                              : `原业务时间未知；接入于 ${dateTime(event.recorded_at)}`
                          }}
                          · #{{ event.sequence }}
                        </p>
                        <p v-if="event.reason">原因：{{ event.reason }}</p>
                        <p v-if="event.details.corrects_event_id" class="ops-subtle">
                          更正原事件 #{{ event.details.corrects_event_id }}（顺序 #{{
                            event.details.corrects_sequence
                          }}）；原业务记录保留。
                        </p>
                        <details
                          v-if="event.changed_fields && Object.keys(event.changed_fields).length"
                          class="ops-advanced"
                        >
                          <summary>查看变更与关联标识</summary>
                          <p v-for="(change, field) in event.changed_fields" :key="field">
                            {{ field === 'status' ? '订单状态' : '事件说明' }}：{{
                              field === 'status'
                                ? statusLabel[change.before as OrderStatus]
                                : change.before
                            }}
                            →
                            {{
                              field === 'status'
                                ? statusLabel[change.after as OrderStatus]
                                : change.after
                            }}
                          </p>
                          <small>关联标识：{{ event.correlation_id }}</small>
                        </details>
                        <small v-if="event.details.receipt_id"
                          >收款凭据 #{{ event.details.receipt_id }} ·
                          {{ money(event.details.amount) }}</small
                        ><small v-if="event.details.movement_ids?.length"
                          >关联库存流水 {{ event.details.movement_ids.join('、') }}</small
                        ><a
                          v-if="event.details.print_job_id"
                          :href="`${API_BASE}/order-operations/print-jobs/${event.details.print_job_id}/html?document_id=${event.details.document_id}&layout=${event.details.layout || 'orders'}`"
                          target="_blank"
                          rel="noopener"
                          >查看文档快照</a
                        >
                        <button
                          class="text-action"
                          :disabled="saving"
                          @click="openCorrection(event)"
                        >
                          追加更正说明
                        </button>
                      </li>
                    </ol>
                    <div class="ops-mini-pages">
                      <button :disabled="eventPage <= 1" @click="changeEvidencePage('event', -1)">
                        上一页</button
                      ><span>{{ eventPage }} / {{ Math.max(1, Math.ceil(eventTotal / 20)) }}</span
                      ><button
                        :disabled="eventPage * 20 >= eventTotal"
                        @click="changeEvidencePage('event', 1)"
                      >
                        下一页
                      </button>
                    </div>
                    <form class="order-note-form" @submit.prevent="appendNote">
                      <label class="form-field"
                        ><span>追加内部备注（仅后台可见）</span
                        ><textarea v-model="internalNote" maxlength="2000" required /></label
                      ><button class="ops-button" :disabled="saving || !internalNote.trim()">
                        追加并留痕
                      </button>
                    </form>
                  </section>
                  <section>
                    <h3>关联库存流水</h3>
                    <p
                      v-for="movement in detailMovements"
                      :key="movement.id"
                      class="order-ledger-row"
                    >
                      #{{ movement.id }} · {{ movement.product_name_snapshot }}<br />可售
                      {{ movement.available_before }} → {{ movement.available_after }} · 预留
                      {{ movement.reserved_delta > 0 ? '+' : '' }}{{ movement.reserved_delta }} ·
                      在库 {{ movement.on_hand_delta > 0 ? '+' : '' }}{{ movement.on_hand_delta
                      }}<small
                        >{{ dateTime(movement.occurred_at) }} ·
                        {{ movement.actor_name_snapshot }}</small
                      >
                    </p>
                    <div class="ops-mini-pages">
                      <button
                        :disabled="movementPage <= 1"
                        @click="changeEvidencePage('movement', -1)"
                      >
                        上一页</button
                      ><span
                        >{{ movementPage }} / {{ Math.max(1, Math.ceil(movementTotal / 20)) }}</span
                      ><button
                        :disabled="movementPage * 20 >= movementTotal"
                        @click="changeEvidencePage('movement', 1)"
                      >
                        下一页
                      </button>
                    </div>
                    <h3>打印文档与补打</h3>
                    <p class="ops-subtle">
                      记录请求次数，不能确认实际出纸。查看文档不会登记新打印请求。
                    </p>
                    <p v-if="!documents.length" class="ops-subtle">从未请求打印</p>
                    <div v-for="doc in documents" :key="doc.id" class="print-document-row">
                      <span>请求第 {{ doc.attempt_no }} 次 · 版本 {{ doc.document_revision }}</span
                      ><small :title="doc.document_hash"
                        >摘要 {{ doc.document_hash.slice(0, 16) }}</small
                      ><a
                        :href="`${API_BASE}/order-operations/print-jobs/${doc.job_id}/html?document_id=${doc.id}`"
                        target="_blank"
                        rel="noopener"
                        >查看快照</a
                      ><button
                        v-if="!['cancelled', 'refunded'].includes(selectedOrder.status)"
                        class="text-action"
                        @click="openPrint([selectedOrder], doc)"
                      >
                        补打此版本
                      </button>
                    </div>
                    <div class="ops-mini-pages">
                      <button
                        :disabled="documentPage <= 1"
                        @click="changeEvidencePage('document', -1)"
                      >
                        上一页</button
                      ><span
                        >{{ documentPage }} / {{ Math.max(1, Math.ceil(documentTotal / 20)) }}</span
                      ><button
                        :disabled="documentPage * 20 >= documentTotal"
                        @click="changeEvidencePage('document', 1)"
                      >
                        下一页
                      </button>
                    </div>
                  </section>
                </div>
              </template>
            </div>
          </article>
        </div>
        <AppPagination
          v-if="!error"
          :page="page"
          :page-size="pageSize"
          :page-count="pageCount"
          :total="total"
          :loading="loading"
          @change="changePage"
        />
      </section>
    </section>

    <div v-if="dialogOpen" class="product-dialog-backdrop" @click.self="closeDialog">
      <section
        class="product-dialog order-create-dialog"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="'order-form-title'"
      >
        <header class="product-dialog-header">
          <div>
            <h2 id="order-form-title">{{ t('Create order') }}</h2>
            <p>{{ t('Creating an order reserves the selected inventory.') }}</p>
          </div>
          <button class="dialog-close" type="button" :aria-label="t('Cancel')" @click="closeDialog">
            <X :size="18" />
          </button>
        </header>
        <form class="product-form" @submit.prevent="saveOrder">
          <p v-if="dialogLoading" class="module-empty">{{ t('Loading order form…') }}</p>
          <template v-else>
            <div class="product-form-grid">
              <label class="form-field"
                ><span>{{ t('Customer') }}</span
                ><select :value="selectedCustomerId ?? ''" required @change="changeCustomer">
                  <option value="" disabled>{{ t('Choose customer') }}</option>
                  <option v-for="customer in customers" :key="customer.id" :value="customer.id">
                    {{ customer.name }} · {{ customer.phone }}
                  </option>
                </select></label
              >
              <label class="form-field"
                ><span>{{ t('Shipping address') }}</span
                ><select v-model.number="selectedAddressId" required>
                  <option v-for="address in addresses" :key="address.id" :value="address.id">
                    {{ address.recipient_name }} · {{ address.city }}{{ address.district
                    }}{{ address.detail }}
                  </option>
                </select></label
              >
            </div>

            <div class="order-lines-heading">
              <strong>{{ t('Order items') }}</strong
              ><button class="text-action" type="button" @click="addLineItem">
                <Plus :size="14" /> {{ t('Add item') }}
              </button>
            </div>
            <div class="order-line-list">
              <div v-for="(line, index) in lineItems" :key="index" class="order-line-row">
                <div class="form-field">
                  <label
                    ><span>{{ t('Product') }}</span
                    ><select
                      v-model.number="line.product_id"
                      required
                      @change="chooseLineProduct(line)"
                    >
                      <option :value="null">{{ t('Choose product') }}</option>
                      <option v-for="product in products" :key="product.id" :value="product.id">
                        {{ product.name }} · ¥{{ product.price.toFixed(2) }} · {{ t('Stock') }}
                        {{ product.stock }}
                      </option>
                    </select></label
                  >
                  <select
                    v-if="line.product_id != null"
                    v-model.number="line.sku_id"
                    required
                    :disabled="loadingSKUs.includes(line.product_id)"
                    :aria-label="`第 ${index + 1} 行商品规格`"
                  >
                    <option :value="null">
                      {{
                        loadingSKUs.includes(line.product_id)
                          ? '正在读取规格…'
                          : '请选择颜色 / 尺寸'
                      }}
                    </option>
                    <option
                      v-for="sku in (skuOptions[line.product_id] ?? []).filter((row) => row.active)"
                      :key="sku.id"
                      :value="sku.id"
                      :disabled="sku.stock <= 0"
                    >
                      {{ sku.label }} · {{ money(sku.price) }} · 可售 {{ sku.stock }}
                    </option>
                  </select>
                </div>
                <label class="form-field"
                  ><span>{{ t('Quantity') }}</span
                  ><input v-model.number="line.quantity" type="number" min="1" step="1" required
                /></label>
                <button
                  class="icon-button remove-line-button"
                  type="button"
                  :disabled="lineItems.length === 1"
                  :aria-label="t('Remove item')"
                  @click="removeLineItem(index)"
                >
                  <Trash2 :size="16" />
                </button>
              </div>
            </div>

            <div class="product-form-grid order-extra-fields">
              <label class="form-field"
                ><span>{{ t('Shipping fee') }}</span
                ><input v-model="shippingFee" type="number" min="0" step="0.01" required
              /></label>
              <label class="form-field"
                ><span>{{ t('Order note') }}</span
                ><input v-model="note" maxlength="2000"
              /></label>
            </div>
            <div class="order-total-row">
              <span>{{ t('Subtotal') }}</span
              ><strong>{{ money(subtotal) }}</strong
              ><span>{{ t('Total') }}</span
              ><strong>{{ money(formTotal) }}</strong>
            </div>
          </template>
          <p v-if="dialogError" class="form-error" role="alert">{{ dialogError }}</p>
          <p v-if="skuError" class="form-error" role="alert">{{ skuError }}</p>
          <p v-if="!canSubmit && !dialogLoading && products.length" class="form-hint">
            {{ t('Choose valid products and quantities within available stock.') }}
          </p>
          <footer class="product-dialog-footer">
            <span></span>
            <div>
              <button
                class="secondary-action"
                type="button"
                :disabled="saving"
                @click="closeDialog"
              >
                {{ t('Cancel') }}</button
              ><button class="primary-action" type="submit" :disabled="!canSubmit">
                {{ saving ? t('Saving…') : t('Create order') }}
              </button>
            </div>
          </footer>
        </form>
      </section>
    </div>
    <div
      v-if="correctionTarget"
      class="product-dialog-backdrop"
      @click.self="!saving && (correctionTarget = null)"
    >
      <section
        class="product-dialog ops-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="correction-title"
      >
        <header class="product-dialog-header">
          <h2 id="correction-title">追加事件更正说明</h2>
          <button
            class="icon-button"
            aria-label="关闭"
            :disabled="saving"
            @click="correctionTarget = null"
          >
            <X :size="18" />
          </button>
        </header>
        <form class="product-form" @submit.prevent="saveCorrection">
          <p class="ops-notice">
            原事件 #{{ correctionTarget.id }} ·
            {{ correctionTarget.summary }}。更正只追加说明，不回写原记录、业务时间或库存。
          </p>
          <label class="form-field"
            ><span>更正内容</span><textarea v-model="correctionContent" maxlength="2000" required />
          </label>
          <label class="form-field"
            ><span>更正原因</span><textarea v-model="correctionReason" maxlength="1000" required />
          </label>
          <p v-if="detailError" class="ops-error" role="alert">{{ detailError }}</p>
          <button
            class="ops-button primary"
            :disabled="saving || !correctionContent.trim() || !correctionReason.trim()"
          >
            追加并保留原事件
          </button>
        </form>
      </section>
    </div>
    <div
      v-if="printOpen"
      class="product-dialog-backdrop"
      @click.self="!printSaving && (printOpen = false)"
    >
      <section
        class="product-dialog ops-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="print-title"
      >
        <header class="product-dialog-header">
          <h2 id="print-title">请求{{ reprintRequired ? '补打' : '打印' }}配货单</h2>
          <button
            class="icon-button"
            aria-label="关闭"
            :disabled="printSaving"
            @click="printOpen = false"
          >
            <X :size="18" />
          </button>
        </header>
        <form class="product-form" @submit.prevent="submitPrint">
          <p class="ops-notice">
            共
            {{ printOrders.length }}
            单。{{
              printLayout === 'summary'
                ? '按商品编码及名称快照合并拣货，仍需逐单复核。'
                : '每单独立分页。'
            }}整批校验，任何订单冲突则全部不生成。打印不会改变订单或库存。
          </p>
          <label class="form-field"
            ><span>打印内容</span
            ><select v-model="printLayout" :disabled="printSaving">
              <option value="orders">逐单配货单</option>
              <option value="summary">合并拣货汇总单</option>
            </select></label
          >
          <ul class="print-order-list">
            <li v-for="row in printOrders" :key="row.id">
              {{ row.order_number }} · {{ statusLabel[row.status] }} ·
              {{ printSources[row.id] ? `补打旧文档 #${printSources[row.id]}` : '生成最新快照' }}
            </li>
          </ul>
          <label class="form-field"
            ><span>{{
              reprintRequired ? '补打原因（必填）' : '补打原因（如已请求过打印，请填写）'
            }}</span
            ><textarea
              v-model="printReason"
              maxlength="1000"
              :required="reprintRequired"
              :disabled="printSaving"
            /></label
          ><template v-if="!Object.keys(printSources).length"
            ><label class="form-field"
              ><span>仓内配货备注（仅此字段会打印，内部讨论不自动带入）</span
              ><textarea v-model="pickingNote" maxlength="300" :disabled="printSaving" /></label
            ><label v-if="printLayout === 'orders'" class="ops-inline-field"
              ><input v-model="includeAmount" type="checkbox" :disabled="printSaving" />
              在配货单中显示金额</label
            ></template
          >
          <p v-else class="ops-subtle">沿用旧文档的内容及金额显示选项；文档摘要保持一致。</p>
          <p class="ops-subtle">浏览器取消打印仍会保留“请求打印”记录，不能据此判断出纸。</p>
          <p v-if="printError" class="ops-error" role="alert">{{ printError }}</p>
          <footer class="product-dialog-footer">
            <button
              class="ops-button"
              type="button"
              :disabled="printSaving"
              @click="printOpen = false"
            >
              返回</button
            ><button class="ops-button primary" :disabled="printSaving">
              {{ printSaving ? '正在生成…' : '登记请求并打开打印' }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </main>
</template>
