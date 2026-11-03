<script setup lang="ts">
// 批量工作流：先读取包裹快照核对，再逐单提交结果；部分失败不代表成功订单需要重新出库。
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import type { OrderRecord } from '../../orders/model/types'
import { fetchPackages } from '../api/parcelsApi'
import { saveShipment } from '../api/shippingApi'
import { request } from '../../../shared/api/httpClient'
import type { PackageSnapshot } from '../model/parcels'
import { populateLabelPrintWindow, type PrintableLabel } from '../api/labelPrinting'

const props = defineProps<{ orders: OrderRecord[] }>()
const emit = defineEmits<{ changed: [] }>()
const busy = ref(false)
const error = ref('')
type PreviewRow = { order: OrderRecord; packages: PackageSnapshot }
const preview = ref<PreviewRow[]>([])
const hasShipmentHold = (row: PreviewRow) =>
  !!row.order.aftersale_pending ||
  !!row.packages.aftersale_pending ||
  row.packages.label_tasks.some((task) => task.status === 'requesting' || task.status === 'unknown')
const results = ref<{ order_id: number; success: boolean; error: string | null }[]>([])
const labelResults = ref<{ order_id: number; message: string }[]>([])
const labelDraft = reactive({ carrier_name: '顺丰速运', weight: '1.000' })
const configured = ref(false)
const settingsError = ref('')
const manualDrafts = reactive<Record<number, { carrier_name: string; tracking_number: string }>>({})
const manualResults = ref<{ order_id: number; message: string }[]>([])
const manualCount = computed(
  () =>
    missingWaybills.value.filter((row) => manualDrafts[row.order.id]?.tracking_number.trim())
      .length,
)
const labelKeys = new Map<number, { hash: string; key: string }>()
const readyToDispatch = computed(
  // 取号结果不确定时必须先核实，避免把平台可能已发出的运单遗漏在交接清单之外。
  () =>
    preview.value.length > 0 &&
    preview.value.every((row) => row.packages.items.length && !hasShipmentHold(row)),
)
const printableOrders = computed(() =>
  preview.value.filter(
    (row) =>
      row.packages.items.length &&
      !hasShipmentHold(row) &&
      row.packages.items.every((parcel) => parcel.label?.status === 'issued'),
  ),
)
const readyToPrint = computed(() => printableOrders.value.length > 0)
const missingWaybills = computed(() => preview.value.filter((row) => !row.packages.items.length))
let batchKey = crypto.randomUUID()
let batchHash = ''
let generation = 0
let disposed = false
watch(
  () =>
    props.orders
      .map((o) => `${o.id}:${o.shipment?.revision}:${o.status}:${o.aftersale_pending}`)
      .join(','),
  () => {
    generation++
    if (
      preview.value.length !== props.orders.length ||
      preview.value.some((row) => {
        const order = props.orders.find((order) => order.id === row.order.id)
        return (
          !order ||
          order.status !== row.order.status ||
          order.aftersale_pending ||
          (order.shipment?.revision || 0) !== row.packages.revision
        )
      })
    ) {
      preview.value = []
      error.value = settingsError.value = ''
      results.value = []
      labelResults.value = []
      manualResults.value = []
    }
  },
)
async function prepare() {
  // 并行读取选中订单的包裹，并与选择时的运单版本比较，拒绝使用过期预览。
  if (!props.orders.length || busy.value) return
  busy.value = true
  error.value = ''
  results.value = []
  preview.value = []
  configured.value = false
  settingsError.value = ''
  const current = ++generation
  try {
    const snapshots = await Promise.all(
      props.orders.map(async (order) => ({ order, packages: await fetchPackages(order.id) })),
    )
    if (disposed || current !== generation) return
    if (
      snapshots.some(
        (row) =>
          hasShipmentHold(row) || row.packages.revision !== (row.order.shipment?.revision || 0),
      )
    )
      throw new Error('选中订单存在售后暂停、运单变更或未决面单任务，请刷新并重新选择。')
    preview.value = snapshots
    for (const row of snapshots) {
      if (!manualDrafts[row.order.id])
        manualDrafts[row.order.id] = { carrier_name: '顺丰速运', tracking_number: '' }
    }
    try {
      const settings = await request<{ configured: boolean }>('/shipping/label-settings')
      if (!disposed && current === generation) configured.value = settings.configured
    } catch {
      if (!disposed && current === generation)
        settingsError.value = '电子面单配置读取失败，可继续人工登记运单。'
    }
  } catch (reason) {
    if (!disposed && current === generation)
      error.value = reason instanceof Error ? reason.message : '批量核对失败'
  } finally {
    busy.value = false
  }
}
async function saveManualWaybills() {
  if (busy.value || !manualCount.value) return
  const selected = missingWaybills.value.filter((row) =>
    manualDrafts[row.order.id]?.tracking_number.trim(),
  )
  const entries = selected.map((row) => ({
    row,
    carrier_name: manualDrafts[row.order.id]!.carrier_name.trim(),
    tracking_number: manualDrafts[row.order.id]!.tracking_number.trim(),
  }))
  if (
    entries.some(
      (entry) =>
        !entry.carrier_name ||
        entry.carrier_name.length > 80 ||
        !/^[A-Za-z0-9-]{6,80}$/.test(entry.tracking_number),
    )
  ) {
    error.value = '请填写快递公司及 6–80 位运单号（英文字母、数字或连字符）。'
    return
  }
  const waybills = preview.value.flatMap((row) =>
    row.packages.items.map(
      (parcel) => `${parcel.carrier_name.toLowerCase()}:${parcel.tracking_number.toLowerCase()}`,
    ),
  )
  waybills.push(
    ...entries.map(
      (entry) => `${entry.carrier_name.toLowerCase()}:${entry.tracking_number.toLowerCase()}`,
    ),
  )
  if (new Set(waybills).size !== waybills.length) {
    error.value = '同一家快递公司的运单号不能重复分配给不同订单，请核对。'
    return
  }
  busy.value = true
  error.value = ''
  manualResults.value = []
  const current = generation
  try {
    for (const entry of entries) {
      if (disposed || current !== generation) break
      try {
        await saveShipment(entry.row.order.id, {
          carrier_name: entry.carrier_name,
          tracking_number: entry.tracking_number,
          remark: '',
          expected_revision: entry.row.packages.revision,
        })
        const snapshot = await fetchPackages(entry.row.order.id)
        if (disposed || current !== generation) break
        entry.row.packages = snapshot
        manualResults.value.push({ order_id: entry.row.order.id, message: '运单已登记，尚未出库' })
      } catch (reason) {
        if (disposed || current !== generation) break
        manualResults.value.push({
          order_id: entry.row.order.id,
          message: reason instanceof Error ? reason.message : '登记失败，可重试',
        })
      }
    }
    if (!disposed) emit('changed')
  } finally {
    busy.value = false
  }
}
async function dispatch() {
  if (!readyToDispatch.value || busy.value) return
  if (
    !window.confirm(
      `确认这 ${preview.value.length} 个订单的全部包裹和商品均已实际交给快递？确认后将逐单出库。`,
    )
  )
    return
  const items = preview.value
    .map((row) => ({ order_id: row.order.id, expected_revision: row.packages.revision }))
    .sort((a, b) => a.order_id - b.order_id)
  const hash = JSON.stringify(items)
  // 相同批次内容重试沿用幂等键；订单或版本变化后才生成新的批次身份。
  if (hash !== batchHash) {
    batchHash = hash
    batchKey = crypto.randomUUID()
  }
  busy.value = true
  error.value = ''
  const current = generation
  try {
    const result = await request<{ items: typeof results.value }>(
      '/shipping/dispatch-batch',
      'POST',
      { items, idempotency_key: batchKey },
    )
    if (!disposed && current === generation) {
      results.value = result.items
      if (result.items.every((row) => row.success)) preview.value = []
    }
    if (!disposed) emit('changed')
  } catch (reason) {
    if (!disposed && current === generation)
      error.value = reason instanceof Error ? reason.message : '批量提交失败，重试会沿用本次批次'
  } finally {
    busy.value = false
  }
}
async function takeLabels() {
  if (busy.value || !configured.value || !missingWaybills.value.length) return
  if (
    !window.confirm(
      `为 ${missingWaybills.value.length} 个尚无运单的订单申请真实电子面单？可能消耗平台额度。`,
    )
  )
    return
  const selected = [...missingWaybills.value]
  busy.value = true
  error.value = ''
  labelResults.value = []
  const current = generation
  try {
    for (const row of selected) {
      if (disposed || current !== generation) break
      const body = {
        carrier_name: labelDraft.carrier_name,
        weight: labelDraft.weight,
        expected_revision: row.packages.revision,
      }
      const hash = JSON.stringify(body)
      let intent = labelKeys.get(row.order.id)
      if (!intent || intent.hash !== hash) {
        intent = { hash, key: crypto.randomUUID() }
        labelKeys.set(row.order.id, intent)
      }
      try {
        const task = await request<{ status: string; error: string | null }>(
          `/shipping/orders/${row.order.id}/packages/1/label`,
          'POST',
          { ...body, idempotency_key: intent.key },
        )
        if (task.status === 'failed') labelKeys.delete(row.order.id)
        if (disposed || current !== generation) break
        labelResults.value.push({
          order_id: row.order.id,
          message:
            task.status === 'issued'
              ? '电子面单已取号'
              : task.error || '面单正在申请，请查看订单任务',
        })
      } catch (reason) {
        if (disposed || current !== generation) break
        labelResults.value.push({
          order_id: row.order.id,
          message: reason instanceof Error ? reason.message : '申请失败，重试沿用本次请求',
        })
      }
    }
    if (!disposed) emit('changed')
  } finally {
    busy.value = false
  }
}
async function printLabels() {
  if (busy.value || !readyToPrint.value) return
  const popup = window.open('', '_blank')
  if (!popup) {
    error.value = '请允许打开打印窗口后重试'
    return
  }
  popup.opener = null
  busy.value = true
  error.value = ''
  try {
    const data = await request<{ items: PrintableLabel[] }>(
      '/shipping/labels/print-batch',
      'POST',
      {
        items: printableOrders.value.map((row) => ({
          order_id: row.order.id,
          expected_revision: row.packages.revision,
        })),
        idempotency_key: crypto.randomUUID(),
      },
    )
    populateLabelPrintWindow(popup, data.items)
  } catch (reason) {
    popup.close()
    error.value = reason instanceof Error ? reason.message : '面单打印页加载失败'
  } finally {
    busy.value = false
  }
}
onBeforeUnmount(() => {
  disposed = true
  generation++
})
</script>

<template>
  <section class="bulk-shipping" aria-label="批量发货">
    <div class="fulfillment-card-title">
      <strong>批量发货 · 已选 {{ orders.length }} 单</strong
      ><button class="ops-button" :disabled="busy || !orders.length" @click="prepare">
        核对选中订单
      </button>
    </div>
    <p class="ops-subtle">
      ① 核对选单 → ② 填写已有运单，或申请电子面单 → ③ 实物交接后确认出库。
      登记运单不会出库；使用纸质面单时，无需配置电子面单账号。
    </p>
    <p v-if="!orders.length" class="ops-subtle">请先勾选下方待发货订单。</p>
    <p v-else-if="!preview.length && !busy" class="ops-subtle">
      已选 {{ orders.length }} 单，请点击“核对选中订单”读取最新包裹信息。
    </p>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="preview.length" class="ops-subtle" role="status">
      已核对 {{ preview.length }} 单 · {{ preview.length - missingWaybills.length }} 单已有运单 ·
      {{ missingWaybills.length }} 单待登记。
      {{
        readyToDispatch
          ? '运单已齐备，请核对全部包裹后确认交接。'
          : '全部订单的运单齐备且无未决任务后，才可批量出库。'
      }}
    </p>
    <div v-for="row in preview" :key="row.order.id" class="bulk-preview">
      <strong
        >{{ row.order.order_number }} · {{ row.order.recipient_name }} ·
        {{ row.order.items.reduce((sum, item) => sum + item.quantity, 0) }} 件商品</strong
      >
      <p v-for="parcel in row.packages.items" :key="parcel.package_no">
        包裹 {{ parcel.package_no }} · {{ parcel.carrier_name }} · {{ parcel.tracking_number }}
      </p>
      <p v-if="!row.packages.items.length" class="ops-subtle">
        尚未登记运单，请在下方填写已有运单号；也可申请电子面单。此处每单登记 1
        个包裹，拆包请进入发货详情。
      </p>
      <div v-if="!row.packages.items.length && manualDrafts[row.order.id]" class="manual-waybill">
        <label
          >快递公司<input
            v-model="manualDrafts[row.order.id]!.carrier_name"
            list="fulfillment-carriers"
            maxlength="80"
            :disabled="busy"
            :aria-label="`${row.order.order_number} 快递公司`"
        /></label>
        <label class="grow"
          >已有运单号<input
            v-model="manualDrafts[row.order.id]!.tracking_number"
            placeholder="填写该订单的运单号，每单一个"
            maxlength="80"
            :disabled="busy"
            :aria-label="`${row.order.order_number} 运单号`"
        /></label>
      </div>
    </div>
    <div v-if="missingWaybills.length" class="manual-actions">
      <button
        class="ops-button primary"
        :disabled="busy || !manualCount"
        @click="saveManualWaybills"
      >
        保存 {{ manualCount }} 单已有运单
      </button>
      <span class="ops-subtle">逐单登记，失败项可重试；保存后仍需确认实物交接。</span>
    </div>
    <form v-if="missingWaybills.length" class="ops-filterbar" @submit.prevent="takeLabels">
      <strong>或申请电子面单</strong>
      <label
        >取号快递公司<input
          v-model="labelDraft.carrier_name"
          list="fulfillment-carriers"
          required
          :disabled="busy"
      /></label>
      <label
        >每包重量（kg）<input
          v-model="labelDraft.weight"
          type="number"
          min="0.001"
          max="1000"
          step="0.001"
          required
          :disabled="busy"
      /></label>
      <button class="ops-button" :disabled="busy || !configured">
        为 {{ missingWaybills.length }} 单批量申请面单
      </button>
      <p v-if="!configured" class="ops-subtle">
        {{
          settingsError ||
          '尚未配置快递100电子面单账号。可直接在上方填写已有运单，无需申请电子面单。'
        }}
      </p>
    </form>
    <button
      v-if="readyToPrint"
      class="ops-button"
      :disabled="busy || !readyToPrint"
      @click="printLabels"
    >
      打印 {{ printableOrders.length }} 单电子面单
    </button>
    <p v-if="preview.length && !readyToPrint" class="ops-subtle">
      当前没有可打印的电子面单。人工登记的运单请使用已有纸质面单，不影响确认交接出库。
    </p>
    <button
      v-if="preview.length"
      class="ops-button primary"
      :disabled="busy || !readyToDispatch"
      @click="dispatch"
    >
      {{ busy ? '正在提交…' : '确认全部包裹已交接并批量出库' }}
    </button>
    <ul v-if="manualResults.length" aria-live="polite">
      <li v-for="row in manualResults" :key="row.order_id">
        订单 #{{ row.order_id }}：{{ row.message }}
      </li>
    </ul>
    <ul v-if="labelResults.length" aria-live="polite">
      <li v-for="row in labelResults" :key="row.order_id">
        订单 #{{ row.order_id }}：{{ row.message }}
      </li>
    </ul>
    <ul v-if="results.length" aria-live="polite">
      <li v-for="row in results" :key="row.order_id">
        订单 #{{ row.order_id }}：{{ row.success ? '出库成功' : row.error }}
      </li>
    </ul>
  </section>
</template>

<style scoped>
.bulk-shipping {
  padding: 18px;
  border: 1px solid var(--border, #e4e7ec);
  border-radius: 12px;
  margin: 16px 0;
}
.bulk-preview {
  padding: 12px 0;
  border-top: 1px solid var(--border, #e4e7ec);
  overflow-wrap: anywhere;
}
.manual-waybill,
.manual-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin: 12px 0;
}
.manual-waybill label {
  display: grid;
  gap: 6px;
  min-width: 180px;
  color: #596763;
  font-size: 12px;
}
.manual-waybill .grow {
  flex: 1;
}
.manual-waybill input {
  width: 100%;
  min-height: 38px;
  padding: 8px 10px;
  border: 1px solid var(--border, #e4e7ec);
  border-radius: 8px;
  font: inherit;
}
</style>
