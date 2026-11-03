<script setup lang="ts">
// 库存中心：查询可用/预留库存、查看流水与告警，并通过“草稿 → 确认”完成整单补货。
import AdjustmentPanel from '../features/inventory/components/AdjustmentPanel.vue'
import CopilotWorkbench from '../features/inventory/components/CopilotWorkbench.vue'
import ProductSKUSelect from '../features/products/components/ProductSKUSelect.vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Download, Plus, RefreshCw, X } from '@lucide/vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import AppPagination from '../shared/components/AppPagination.vue'
import { request } from '../shared/api/httpClient'
import { downloadCsv } from '../shared/api/download'
import { query } from '../shared/api/query'
import type { Alert, Inbound, InventoryRow, Movement } from '../features/inventory/model/types'
import type { Page } from '../shared/api/types'
import { dateTime, dayBoundary, addDays } from '../shared/commerce'

defineProps<{ username: string }>()
const initial = new URLSearchParams(window.location.search)
const tab = ref(initial.get('tab') || 'stock')
const search = ref(initial.get('q') || '')
const category = ref(initial.get('category') || '')
const severity = ref(initial.get('severity') || '')
const productId = ref(initial.get('product_id') || '')
const movementType = ref(initial.get('movement_type') || '')
const referenceNumber = ref(initial.get('reference_number') || '')
const actor = ref(initial.get('actor') || '')
const startDate = ref(initial.get('start_date') || '')
const endDate = ref(initial.get('end_date') || '')
const alertStatus = ref(initial.get('alert_status') || 'active')
const page = ref(Number(initial.get('page')) || 1)
const total = ref(0)
const loading = ref(false)
const saving = ref(false)
const exporting = ref(false)
const error = ref('')
const lastUpdated = ref('')
const stock = ref<InventoryRow[]>([])
const movements = ref<Movement[]>([])
const inbounds = ref<Inbound[]>([])
const alerts = ref<Alert[]>([])
const defaultThreshold = ref(5)
const defaultDraft = ref(5)
const categories = ref<string[]>([])
const ruleProduct = ref<InventoryRow | null>(null)
const ruleMonitor = ref(true)
const ruleThreshold = ref('')
const ruleHistory = ref<
  {
    id: number
    occurred_at: string
    actor_name_snapshot: string
    before: { monitor_enabled?: boolean; threshold_override?: number | null }
    after: { monitor_enabled?: boolean; threshold_override?: number | null }
  }[]
>([])
const receipt = ref<Inbound | null>(null)
const inboundOpen = ref(false)
const inboundReady = ref(false)
const inboundError = ref('')
const remark = ref('')
const lines = ref<{ product_id: number | null; sku_id?: number | null; quantity: number | '' }[]>([
  { product_id: null, quantity: '' },
])
const productChoices = ref<InventoryRow[]>([])
const productSearch = ref('')
const savedSignature = ref('')
let inboundKey = ''
let loadId = 0
let choicesId = 0
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const signature = computed(() => JSON.stringify({ remark: remark.value, items: lines.value }))
// 读取失败时保持不可编辑；已确认/作废的单据也不能退回草稿继续修改。
const editable = computed(
  () => inboundReady.value && (!receipt.value || receipt.value.status === 'draft'),
)
const totalQuantity = computed(() =>
  lines.value.reduce(
    (sum, line) => sum + (Number.isInteger(line.quantity) ? Number(line.quantity) : 0),
    0,
  ),
)
const productCount = computed(
  () => new Set(lines.value.map((line) => line.product_id).filter(Boolean)).size,
)
const severityNames: Record<string, string> = {
  low_stock: '低库存',
  out_of_stock: '缺货',
  normal: '正常',
  unmonitored: '未监控',
}
const movementNames: Record<string, string> = {
  opening: '期初结转',
  stock_count: '盘点调整',
  stock_damage: '报损出库',
  inbound_correction: '入库更正',
  return_received: '退件验收入库',
  order_reserve: '订单预留',
  release: '预留释放',
  dispatch: '配送出库',
  inbound: '补货入库',
}
const statusNames: Record<string, string> = {
  draft: '草稿',
  confirmed: '已确认',
  void: '已作废',
  active: '处理中',
  resolved: '已恢复',
  closed: '已关闭',
}

function saveUrl() {
  if (tab.value === 'copilot') {
    const params = new URLSearchParams(window.location.search)
    params.set('tab', 'copilot')
    window.history.replaceState(null, '', `/inventory?${params}`)
    return
  }
  window.history.replaceState(
    null,
    '',
    `/inventory?${query({ tab: tab.value, q: search.value, category: category.value, severity: severity.value, product_id: productId.value, movement_type: movementType.value, reference_number: referenceNumber.value, actor: actor.value, start_date: startDate.value, end_date: endDate.value, alert_status: alertStatus.value, page: page.value })}`,
  )
}
function movementFilters() {
  // 列表和导出复用相同条件；用户选的结束日转为次日零点，后端使用左闭右开区间。
  return {
    product_id: productId.value,
    movement_type: movementType.value,
    reference_number: referenceNumber.value,
    actor: actor.value,
    start_at: startDate.value ? dayBoundary(startDate.value) : '',
    end_at: endDate.value ? dayBoundary(addDays(endDate.value, 1)) : '',
  }
}
async function exportCsv() {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  try {
    const path =
      tab.value === 'movements'
        ? `/inventory/movements/export?${query(movementFilters())}`
        : '/inventory/inbounds/export'
    await downloadCsv(path, tab.value === 'movements' ? '库存流水.csv' : '补货入库单.csv')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  } finally {
    exporting.value = false
  }
}
async function load() {
  // 快速切换标签或筛选会产生重叠请求，仅允许最后一次请求更新当前列表。
  const id = ++loadId
  loading.value = true
  error.value = ''
  saveUrl()
  try {
    let count = 0
    if (tab.value === 'copilot') {
      total.value = 0
      return
    } else if (tab.value === 'stock') {
      const result = await request<Page<InventoryRow>>(
        `/inventory?${query({ q: search.value, category: category.value, severity: severity.value, page: page.value })}`,
      )
      if (id !== loadId) return
      stock.value = result.items
      count = result.total
    } else if (tab.value === 'movements') {
      const result = await request<Page<Movement>>(
        `/inventory/movements?${query({
          ...movementFilters(),
          page: page.value,
        })}`,
      )
      if (id !== loadId) return
      movements.value = result.items
      count = result.total
    } else if (tab.value === 'inbounds') {
      const result = await request<Page<Inbound>>(`/inventory/inbounds?page=${page.value}`)
      if (id !== loadId) return
      inbounds.value = result.items
      count = result.total
    } else {
      const result = await request<Page<Alert>>(
        `/inventory/alerts?${query({ alert_status: alertStatus.value, severity: severity.value === 'low_stock' || severity.value === 'out_of_stock' ? severity.value : '', page: page.value })}`,
      )
      if (id !== loadId) return
      alerts.value = result.items
      count = result.total
    }
    total.value = count
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await load()
      return
    }
    lastUpdated.value = new Date().toISOString()
  } catch (reason) {
    if (id === loadId) error.value = reason instanceof Error ? reason.message : '库存数据加载失败'
  } finally {
    if (id === loadId) loading.value = false
  }
}
function apply() {
  page.value = 1
  void load()
}
function switchTab(value: string) {
  tab.value = value
  apply()
}
function changePage(value: number) {
  page.value = value
  void load()
}
function viewMovements(id: number) {
  productId.value = String(id)
  switchTab('movements')
}
async function saveDefault() {
  saving.value = true
  error.value = ''
  try {
    await request('/inventory/settings', 'PUT', { threshold: defaultDraft.value })
    defaultThreshold.value = defaultDraft.value
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '默认阈值更新失败'
  } finally {
    saving.value = false
  }
}
async function openRule(row: InventoryRow) {
  ruleProduct.value = row
  ruleMonitor.value = row.monitor_enabled
  ruleThreshold.value = row.threshold_override == null ? '' : String(row.threshold_override)
  ruleHistory.value = []
  try {
    const result = await request<Page<(typeof ruleHistory.value)[number]>>(
      `/inventory/rule-events?product_id=${row.product_id}`,
    )
    if (ruleProduct.value?.product_id === row.product_id) ruleHistory.value = result.items
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '规则记录加载失败'
  }
}
async function saveRule() {
  if (!ruleProduct.value) return
  saving.value = true
  error.value = ''
  try {
    await request(`/inventory/products/${ruleProduct.value.product_id}/rule`, 'PUT', {
      monitor_enabled: ruleMonitor.value,
      threshold_override: ruleThreshold.value === '' ? null : Number(ruleThreshold.value),
    })
    ruleProduct.value = null
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '规则保存失败'
  } finally {
    saving.value = false
  }
}
async function acknowledge(id: number) {
  saving.value = true
  error.value = ''
  try {
    await request(`/inventory/alerts/${id}/acknowledge`, 'POST')
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '已读登记失败'
  } finally {
    saving.value = false
  }
}
async function searchProducts() {
  const id = ++choicesId
  try {
    const result = await request<Page<InventoryRow>>(
      `/inventory?${query({ q: productSearch.value, page_size: 100 })}`,
    )
    if (id !== choicesId) return
    const keep = productChoices.value.filter((row) =>
      lines.value.some((line) => line.product_id === row.product_id),
    )
    productChoices.value = [
      ...new Map([...keep, ...result.items].map((row) => [row.product_id, row])).values(),
    ]
  } catch (reason) {
    inboundError.value = reason instanceof Error ? reason.message : '商品加载失败'
  }
}
async function openInbound(id?: number, existingId?: number) {
  if (saving.value) return
  inboundOpen.value = true
  inboundReady.value = false
  inboundError.value = ''
  receipt.value = null
  remark.value = ''
  productSearch.value = ''
  productChoices.value = []
  lines.value = [{ product_id: id ?? null, quantity: '' }]
  inboundKey = crypto.randomUUID()
  savedSignature.value = ''
  saving.value = true
  try {
    if (existingId) {
      const row = await request<Inbound>(`/inventory/inbounds/${existingId}`)
      receipt.value = row
      remark.value = row.remark
      lines.value = row.items.map((line) => ({
        product_id: line.product_id,
        ...(line.sku_id != null ? { sku_id: line.sku_id } : {}),
        quantity: line.quantity,
      }))
      productChoices.value = row.items.map(
        (line) =>
          ({ product_id: line.product_id, name: line.product_name_snapshot }) as InventoryRow,
      )
      savedSignature.value = signature.value
    }
    inboundReady.value = true
    if (editable.value) {
      if (id) productSearch.value = String(id)
      await searchProducts()
    }
  } catch (reason) {
    inboundError.value = reason instanceof Error ? reason.message : '入库单加载失败'
  } finally {
    saving.value = false
  }
}
async function saveInbound() {
  // 保存草稿只保存单据，不增加库存；真正的库存变更发生在确认接口。
  if (saving.value || !editable.value) return
  if (
    lines.value.some(
      (line) =>
        !line.product_id ||
        !Number.isInteger(line.quantity) ||
        Number(line.quantity) <= 0 ||
        Number(line.quantity) > 1000000,
    )
  ) {
    inboundError.value = '每行需要选择商品，并填写正整数数量'
    return
  }
  saving.value = true
  inboundError.value = ''
  try {
    const row = await request<Inbound>(
      receipt.value ? `/inventory/inbounds/${receipt.value.id}` : '/inventory/inbounds',
      receipt.value ? 'PUT' : 'POST',
      { remark: remark.value, items: lines.value, idempotency_key: inboundKey },
    )
    receipt.value = row
    lines.value = row.items.map((line) => ({
      product_id: line.product_id,
      ...(line.sku_id != null ? { sku_id: line.sku_id } : {}),
      quantity: line.quantity,
    }))
    savedSignature.value = signature.value
    await load()
  } catch (reason) {
    inboundError.value = reason instanceof Error ? reason.message : '草稿保存失败'
  } finally {
    saving.value = false
  }
}
async function confirmInbound() {
  // 必须先保存当前版本，才能确认；signature 用于发现表单里尚未保存的修改。
  if (saving.value || !editable.value) return
  if (
    !receipt.value ||
    signature.value !== savedSignature.value ||
    !window.confirm(
      `确认以下 ${productCount.value} 种商品、共 ${totalQuantity.value} 件全部入库？确认后单据只读。`,
    )
  )
    return
  saving.value = true
  inboundError.value = ''
  try {
    receipt.value = await request<Inbound>(
      `/inventory/inbounds/${receipt.value.id}/confirm`,
      'POST',
    )
    await load()
  } catch (reason) {
    inboundError.value = reason instanceof Error ? reason.message : '整单入库失败'
  } finally {
    saving.value = false
  }
}
async function voidInbound() {
  if (saving.value || !editable.value) return
  if (!receipt.value || !window.confirm('确认作废这张草稿？库存不会变化。')) return
  saving.value = true
  inboundError.value = ''
  try {
    await request(`/inventory/inbounds/${receipt.value.id}`, 'DELETE')
    inboundOpen.value = false
    await load()
  } catch (reason) {
    inboundError.value = reason instanceof Error ? reason.message : '作废失败'
  } finally {
    saving.value = false
  }
}
function restoreUrl() {
  const params = new URLSearchParams(window.location.search)
  tab.value = params.get('tab') || 'stock'
  search.value = params.get('q') || ''
  category.value = params.get('category') || ''
  severity.value = params.get('severity') || ''
  productId.value = params.get('product_id') || ''
  movementType.value = params.get('movement_type') || ''
  referenceNumber.value = params.get('reference_number') || ''
  actor.value = params.get('actor') || ''
  startDate.value = params.get('start_date') || ''
  endDate.value = params.get('end_date') || ''
  alertStatus.value = params.get('alert_status') || 'active'
  page.value = Number(params.get('page')) || 1
  void load()
}
onMounted(async () => {
  void load()
  window.addEventListener('popstate', restoreUrl)
  try {
    const [settings, options] = await Promise.all([
      request<{ default_threshold: number }>('/inventory/settings'),
      request<{ categories: string[] }>('/products/options'),
    ])
    defaultThreshold.value = settings.default_threshold
    defaultDraft.value = settings.default_threshold
    categories.value = options.categories
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '库存配置读取失败'
  }
})
onBeforeUnmount(() => {
  loadId++
  choicesId++
  window.removeEventListener('popstate', restoreUrl)
})
</script>

<template>
  <OperationsShell
    :active-page="tab === 'copilot' ? 'copilot' : 'inventory'"
    title="库存中心"
    :username="username"
  >
    <div class="ops-heading">
      <div>
        <p class="ops-eyebrow">INVENTORY CENTER</p>
        <h1>库存中心</h1>
        <p>每一件库存，都有来处与去向。</p>
      </div>
      <div class="section-actions">
        <button
          v-if="tab === 'movements' || tab === 'inbounds'"
          class="ops-button"
          :disabled="loading || exporting"
          @click="exportCsv"
        >
          <Download :size="16" /> {{ exporting ? '正在导出…' : '导出 CSV' }}
        </button>
        <button class="ops-button" :disabled="loading" @click="load">
          <RefreshCw :size="16" /> 刷新</button
        ><button class="ops-button primary" :disabled="saving" @click="openInbound()">
          <Plus :size="16" /> 新建补货单
        </button>
      </div>
    </div>
    <AdjustmentPanel @changed="load" />
    <div class="ops-tabs">
      <button
        v-for="item in [
          { id: 'stock', name: '库存总览' },
          { id: 'copilot', name: '运营建议' },
          { id: 'movements', name: '库存流水' },
          { id: 'inbounds', name: '补货入库' },
          { id: 'alerts', name: '低库存提醒' },
        ]"
        :key="item.id"
        :class="{ active: tab === item.id }"
        @click="switchTab(item.id)"
      >
        {{ item.name }}
      </button>
    </div>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load">重试</button>
    </p>
    <CopilotWorkbench v-if="tab === 'copilot'" @created="(id) => openInbound(undefined, id)" />
    <form v-if="tab === 'stock'" class="ops-filterbar" @submit.prevent="apply">
      <label>商品名称 / ID<input v-model="search" placeholder="搜索商品" /></label
      ><label
        >分类<select v-model="category">
          <option value="">全部分类</option>
          <option v-for="value in categories" :key="value">{{ value }}</option>
        </select></label
      ><label
        >库存等级<select v-model="severity">
          <option value="">全部</option>
          <option value="risk">全部库存风险</option>
          <option value="low_stock">低库存</option>
          <option value="out_of_stock">缺货</option>
          <option value="normal">正常</option>
        </select></label
      ><button class="ops-button">查询</button>
    </form>
    <form v-if="tab === 'movements'" class="ops-filterbar" @submit.prevent="apply">
      <label>商品 ID<input v-model="productId" type="number" min="1" /></label
      ><label
        >变更类型<select v-model="movementType">
          <option value="">全部</option>
          <option v-for="(name, key) in movementNames" :key="key" :value="key">{{ name }}</option>
        </select></label
      ><label>关联单号<input v-model="referenceNumber" /></label
      ><label>操作账号<input v-model="actor" /></label
      ><label>开始日期<input v-model="startDate" type="date" /></label
      ><label>结束日期<input v-model="endDate" type="date" :min="startDate" /></label
      ><button class="ops-button">查询</button>
    </form>
    <form v-if="tab === 'alerts'" class="ops-filterbar" @submit.prevent="apply">
      <label
        >告警状态<select v-model="alertStatus">
          <option value="active">处理中</option>
          <option value="resolved">已恢复</option>
          <option value="closed">已关闭</option>
        </select></label
      ><label
        >风险等级<select v-model="severity">
          <option value="">全部</option>
          <option value="low_stock">低库存</option>
          <option value="out_of_stock">缺货</option>
        </select></label
      ><button class="ops-button">查询</button
      ><small>已读不会解除风险；库存严格高于阈值才恢复。</small>
    </form>
    <div v-if="tab === 'stock'" class="ops-notice inventory-equation">
      <span><strong>可售 + 预留 = 账面在库</strong> · 单仓 / 件 · 账面数仍需实物盘点核实</span>
      <form class="ops-inline-field" @submit.prevent="saveDefault">
        <label for="default-threshold">默认阈值</label
        ><input
          id="default-threshold"
          v-model.number="defaultDraft"
          type="number"
          min="0"
          max="1000000"
          required
        /><button class="text-action" :disabled="saving">保存</button>
      </form>
    </div>
    <div v-if="tab !== 'copilot'" class="ops-meta">
      <span>共 {{ total }} 条{{ loading ? ' · 正在加载…' : '' }}</span
      ><span v-if="lastUpdated">更新于 {{ dateTime(lastUpdated) }}</span>
    </div>
    <p v-if="tab === 'movements' || tab === 'inbounds'" class="ops-subtle">
      {{
        tab === 'movements'
          ? 'CSV 导出当前筛选条件下的全部流水。'
          : 'CSV 导出全部入库单明细，保留单据中的商品快照。'
      }}
    </p>
    <div v-if="tab !== 'copilot'" class="ops-table-wrap" :aria-busy="loading">
      <table v-if="tab === 'stock'" class="ops-table">
        <thead>
          <tr>
            <th>商品</th>
            <th>可售</th>
            <th>预留</th>
            <th>账面在库</th>
            <th>预警阈值</th>
            <th>等级</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in stock" :key="row.product_id">
            <td>
              <strong>{{ row.name }}</strong
              ><small>#{{ row.product_id }} · {{ row.category }}</small>
            </td>
            <td>
              <strong>{{ row.available_quantity }}</strong>
            </td>
            <td>{{ row.reserved_quantity }}</td>
            <td>{{ row.on_hand_quantity }}</td>
            <td>
              {{ row.effective_threshold
              }}<small>{{ row.threshold_override == null ? '继承默认' : '商品专属' }}</small>
            </td>
            <td>
              <span class="ops-badge" :class="row.severity">{{ severityNames[row.severity] }}</span>
            </td>
            <td>
              <div class="ops-row-actions">
                <button @click="viewMovements(row.product_id)">流水</button
                ><button @click="openRule(row)">规则</button
                ><button @click="openInbound(row.product_id)">补货</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <table v-else-if="tab === 'movements'" class="ops-table">
        <thead>
          <tr>
            <th>时间 / 商品</th>
            <th>类型</th>
            <th>可售：前 → 后</th>
            <th>预留变化</th>
            <th>在库变化</th>
            <th>来源凭据</th>
            <th>操作者 / 原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in movements" :key="row.id">
            <td>
              {{ dateTime(row.occurred_at)
              }}<small>#{{ row.product_id }} {{ row.product_name_snapshot }}</small>
            </td>
            <td>{{ movementNames[row.movement_type] }}</td>
            <td>
              {{ row.available_before }} → <strong>{{ row.available_after }}</strong
              ><small>{{ row.available_delta > 0 ? '+' : '' }}{{ row.available_delta }}</small>
            </td>
            <td>{{ row.reserved_delta > 0 ? '+' : '' }}{{ row.reserved_delta }}</td>
            <td>{{ row.on_hand_delta > 0 ? '+' : '' }}{{ row.on_hand_delta }}</td>
            <td>
              <a
                v-if="row.reference_type === 'order'"
                :href="`/orders?keyword=${encodeURIComponent(row.reference_number)}`"
                >{{ row.reference_number }}</a
              ><button
                v-else-if="row.reference_type === 'inbound'"
                class="text-action"
                @click="openInbound(undefined, row.reference_id)"
              >
                {{ row.reference_number }}</button
              ><span v-else>{{ row.reference_number }}</span>
            </td>
            <td>
              {{ row.actor_name_snapshot }}<small>{{ row.reason || '—' }}</small>
            </td>
          </tr>
        </tbody>
      </table>
      <table v-else-if="tab === 'inbounds'" class="ops-table">
        <thead>
          <tr>
            <th>入库单号</th>
            <th>状态</th>
            <th>录入账号 / 时间</th>
            <th>确认账号 / 时间</th>
            <th>说明</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in inbounds" :key="row.id">
            <td>
              <strong>{{ row.receipt_number }}</strong>
            </td>
            <td>
              <span class="ops-badge" :class="row.status">{{ statusNames[row.status] }}</span>
            </td>
            <td>
              {{ row.created_by }}<small>{{ dateTime(row.created_at) }}</small>
            </td>
            <td>
              {{ row.confirmed_by || '—'
              }}<small>{{ row.confirmed_at ? dateTime(row.confirmed_at) : '尚未确认' }}</small>
            </td>
            <td>{{ row.remark || '—' }}</td>
            <td>
              <button class="text-action" @click="openInbound(undefined, row.id)">
                {{ row.status === 'draft' ? '编辑 / 确认' : '查看单据' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <table v-else class="ops-table">
        <thead>
          <tr>
            <th>商品</th>
            <th>风险等级 / 状态</th>
            <th>触发 / 当前可售</th>
            <th>首次触发</th>
            <th>阅读状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in alerts" :key="row.id">
            <td>
              <strong>{{ row.product_name_snapshot }}</strong
              ><small>#{{ row.product_id }}</small>
            </td>
            <td>
              <span class="ops-badge" :class="row.severity">{{ severityNames[row.severity] }}</span
              ><small>{{ statusNames[row.alert_status] }} {{ row.close_reason || '' }}</small>
            </td>
            <td>{{ row.available_at_trigger }} / {{ row.current_available ?? '已删除' }}</td>
            <td>
              {{ dateTime(row.first_triggered_at)
              }}<small v-if="row.resolved_at">结束于 {{ dateTime(row.resolved_at) }}</small>
            </td>
            <td>{{ row.acknowledged_at ? `${row.acknowledged_by} 已读` : '未读' }}</td>
            <td>
              <div class="ops-row-actions">
                <button v-if="!row.acknowledged_at" :disabled="saving" @click="acknowledge(row.id)">
                  确认已读</button
                ><button @click="viewMovements(row.product_id)">流水</button
                ><button v-if="row.current_available != null" @click="openInbound(row.product_id)">
                  补货
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="tab !== 'copilot' && !total && !loading" class="ops-empty">当前条件下没有记录。</p>
      <p v-if="!total && loading" class="ops-empty">正在加载…</p>
    </div>
    <AppPagination
      v-if="tab !== 'copilot'"
      :page="page"
      :page-size="20"
      :page-count="pageCount"
      :total="total"
      :loading="loading"
      @change="changePage"
    />

    <div
      v-if="ruleProduct"
      class="product-dialog-backdrop"
      @click.self="!saving && (ruleProduct = null)"
    >
      <section
        class="product-dialog ops-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rule-title"
      >
        <header class="product-dialog-header">
          <h2 id="rule-title">库存预警规则 · {{ ruleProduct.name }}</h2>
          <button
            class="icon-button"
            aria-label="关闭"
            :disabled="saving"
            @click="ruleProduct = null"
          >
            <X :size="18" />
          </button>
        </header>
        <form class="product-form" @submit.prevent="saveRule">
          <label class="ops-inline-field"
            ><input v-model="ruleMonitor" type="checkbox" /> 启用商品监控</label
          ><label class="form-field"
            ><span>商品专属阈值（留空继承默认 {{ defaultThreshold }}；0 有效）</span
            ><input v-model="ruleThreshold" type="number" min="0" max="1000000"
          /></label>
          <p class="ops-subtle">仅监控在售商品。缺货与低库存互斥，已读不代表恢复。</p>
          <button class="ops-button primary" :disabled="saving">保存规则</button>
          <h3>最近规则变更</h3>
          <p v-if="!ruleHistory.length" class="ops-subtle">暂无变更记录</p>
          <div v-for="event in ruleHistory" :key="event.id" class="rule-history">
            <strong>{{ event.actor_name_snapshot }}</strong> · {{ dateTime(event.occurred_at) }}
            <p>
              阈值 {{ event.before.threshold_override ?? '默认' }} →
              {{ event.after.threshold_override ?? '默认' }} · 监控
              {{ event.after.monitor_enabled ? '开启' : '关闭' }}
            </p>
          </div>
        </form>
      </section>
    </div>
    <div
      v-if="inboundOpen"
      class="product-dialog-backdrop"
      @click.self="!saving && (inboundOpen = false)"
    >
      <section
        class="product-dialog ops-dialog wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="inbound-title"
      >
        <header class="product-dialog-header">
          <div>
            <h2 id="inbound-title">{{ receipt?.receipt_number || '新建补货入库单' }}</h2>
            <p>
              {{
                !inboundReady
                  ? '正在读取单据…'
                  : receipt
                    ? statusNames[receipt.status]
                    : '先保存草稿，再核对确认入库'
              }}
            </p>
          </div>
          <button
            class="icon-button"
            aria-label="关闭"
            :disabled="saving"
            @click="inboundOpen = false"
          >
            <X :size="18" />
          </button>
        </header>
        <div class="product-form">
          <p v-if="inboundError" class="ops-error" role="alert">{{ inboundError }}</p>
          <p v-if="!inboundReady && !saving" class="ops-subtle">单据未能读取，请关闭后重试。</p>
          <form v-if="editable" class="ops-filterbar" @submit.prevent="searchProducts">
            <label>查找商品<input v-model="productSearch" placeholder="名称或商品 ID" /></label
            ><button class="ops-button" :disabled="saving">搜索</button
            ><small>最多显示 100 项，可输入关键词缩小范围</small>
          </form>
          <div v-for="(line, index) in lines" :key="index" class="inbound-line">
            <div class="form-field">
              <label
                ><span>商品 {{ index + 1 }}</span
                ><select v-model.number="line.product_id" :disabled="!editable || saving">
                  <option :value="null">请选择商品</option>
                  <option
                    v-for="product in productChoices"
                    :key="product.product_id"
                    :value="product.product_id"
                  >
                    #{{ product.product_id }} {{ product.name }}
                  </option>
                </select></label
              ><ProductSKUSelect
                v-if="editable && line.product_id"
                v-model="line.sku_id"
                :product-id="line.product_id"
                :disabled="saving"
              /><small v-else-if="line.sku_id">SKU #{{ line.sku_id }}</small>
            </div>
            <label class="form-field"
              ><span>实际到货 / 件</span
              ><input
                v-model.number="line.quantity"
                type="number"
                min="1"
                placeholder="填写实际到货数量"
                max="1000000"
                step="1"
                :readonly="!editable"
                :disabled="saving" /></label
            ><button
              v-if="editable"
              class="text-action"
              :disabled="lines.length === 1 || saving"
              @click="lines.splice(index, 1)"
            >
              移除
            </button>
          </div>
          <button
            v-if="editable"
            class="text-action"
            :disabled="lines.length >= 100 || saving"
            @click="lines.push({ product_id: null, quantity: '' })"
          >
            ＋ 添加商品（重复商品保存时合并）</button
          ><label class="form-field"
            ><span>入库说明</span
            ><textarea v-model="remark" maxlength="2000" :readonly="!editable" :disabled="saving" />
          </label>
          <p class="ops-notice">
            {{ productCount }} 种商品 · 共 {{ totalQuantity }} 件。确认后整单一次入库，单据只读。
          </p>
          <p v-if="receipt?.confirmed_at">
            确认人 {{ receipt.confirmed_by }} · {{ dateTime(receipt.confirmed_at) }}
          </p>
          <footer v-if="editable" class="product-dialog-footer">
            <button
              v-if="receipt"
              class="text-action danger-text"
              :disabled="saving"
              @click="voidInbound"
            >
              作废草稿
            </button>
            <div>
              <button class="ops-button" :disabled="saving" @click="saveInbound">保存草稿</button
              ><button
                class="ops-button primary"
                :disabled="saving || !receipt || signature !== savedSignature"
                @click="confirmInbound"
              >
                核对并确认入库
              </button>
            </div>
          </footer>
          <p v-if="receipt && signature !== savedSignature && editable" class="ops-subtle">
            存在未保存更改，请先保存草稿。
          </p>
        </div>
      </section>
    </div>
  </OperationsShell>
</template>
