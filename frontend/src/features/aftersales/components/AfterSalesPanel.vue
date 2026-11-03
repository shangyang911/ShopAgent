<script setup lang="ts">
// 订单详情内的售后边界：创建申请、登记实际退款、验收退件各有独立表单。
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { OrderRecord } from '../../orders/model/types'
import type { AfterSale, AfterSaleSnapshot } from '../model/types'
import {
  cancelAfterSale,
  createAfterSale,
  fetchAfterSales,
  inspectReturn,
  writeRefund,
} from '../api/aftersalesApi'
import { dateTime, money } from '../../../shared/commerce'

const props = defineProps<{ order: OrderRecord }>()
const emit = defineEmits<{ changed: [] }>()
const snapshot = ref<AfterSaleSnapshot | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const creating = ref(false)
const draft = reactive({ kind: 'refund' as 'refund' | 'return_refund', amount: '', reason: '' })
const quantities = reactive<Record<number, number>>({})
const refundCase = ref<AfterSale | null>(null)
const refund = reactive({ amount: '', reference: '', occurredAt: '' })
const inspectionCase = ref<AfterSale | null>(null)
const inspection = reactive<Record<number, { accepted: number; damaged: number }>>({})
const inspectionNote = ref('')
let alive = true
let generation = 0
// 同一份内容重试必须沿用提交键；用户改内容时才创建新键。
let creationRequest = { hash: '', key: '' }
let refundRequest = { hash: '', key: '' }
const names = { open: '处理中', closed: '已完成', cancelled: '已撤销' }

async function load() {
  const sequence = ++generation
  try {
    const value = await fetchAfterSales(props.order.id)
    if (alive && sequence === generation) snapshot.value = value
  } catch (reason) {
    if (alive && sequence === generation)
      error.value = reason instanceof Error ? reason.message : '售后记录读取失败'
  }
}
async function mutate(action: () => Promise<unknown>, success: string) {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    await action()
    if (!alive) return
    notice.value = success
    creating.value = false
    refundCase.value = inspectionCase.value = null
    emit('changed')
    await load()
    return true
  } catch (reason) {
    // 写入失败保留输入，用户可以按相同提交键安全重试。
    if (alive) error.value = reason instanceof Error ? reason.message : '售后操作失败'
  } finally {
    if (alive) busy.value = false
  }
}
async function create() {
  const body = {
    kind: draft.kind,
    requested_amount: draft.amount,
    reason: draft.reason,
    items:
      draft.kind === 'return_refund'
        ? props.order.items
            .filter((item) => Number(quantities[item.id]) > 0)
            .map((item) => ({ order_item_id: item.id, quantity: Number(quantities[item.id]) }))
        : [],
  }
  const hash = JSON.stringify(body)
  if (creationRequest.hash !== hash) creationRequest = { hash, key: crypto.randomUUID() }
  if (
    await mutate(
      () => createAfterSale(props.order.id, { ...body, idempotency_key: creationRequest.key }),
      '售后申请已保存，尚未退款或恢复库存。',
    )
  )
    creationRequest = { hash: '', key: '' }
}
function openRefund(row: AfterSale) {
  refundCase.value = row
  inspectionCase.value = null
  refund.amount = ''
  refund.reference = ''
  // datetime-local 按本机时区录入，提交时转为带时区的 ISO 时间。
  const now = new Date()
  refund.occurredAt = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 23)
}
async function recordRefund() {
  const row = refundCase.value
  if (!row) return
  if (!refund.occurredAt || Number.isNaN(new Date(refund.occurredAt).getTime())) {
    error.value = '请填写有效的实际退款时间'
    return
  }
  const body = {
    amount: refund.amount,
    reference: refund.reference,
    occurred_at: new Date(refund.occurredAt).toISOString(),
    expected_revision: row.revision,
  }
  const hash = JSON.stringify({ case: row.id, ...body })
  if (refundRequest.hash !== hash) refundRequest = { hash, key: crypto.randomUUID() }
  if (
    await mutate(
      () => writeRefund(props.order.id, row.id, { ...body, idempotency_key: refundRequest.key }),
      '已登记核实的退款凭据。',
    )
  )
    refundRequest = { hash: '', key: '' }
}
function openInspection(row: AfterSale) {
  inspectionCase.value = row
  refundCase.value = null
  inspectionNote.value = ''
  for (const line of row.items) inspection[line.order_item_id] = { accepted: 0, damaged: 0 }
}
function receive() {
  const row = inspectionCase.value
  if (!row) return
  return mutate(
    () =>
      inspectReturn(props.order.id, row.id, {
        expected_revision: row.revision,
        note: inspectionNote.value,
        items: row.items.map((line) => ({
          order_item_id: line.order_item_id,
          accepted_quantity: Number(inspection[line.order_item_id]!.accepted),
          damaged_quantity: Number(inspection[line.order_item_id]!.damaged),
        })),
      }),
    '验收已保存，只有合格件计入可售库存。',
  )
}
onMounted(load)
onBeforeUnmount(() => {
  alive = false
  generation++
})
</script>

<template>
  <section class="business-panel" aria-label="售后与退款">
    <header class="fulfillment-card-title">
      <h3>售后与退款</h3>
      <button class="ops-button" :disabled="busy" @click="load">刷新记录</button>
    </header>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="snapshot">
      <p>
        已核实收款 {{ money(snapshot.confirmed_paid) }} · 已登记退款
        {{ money(snapshot.refunded_amount) }} · 剩余可退 {{ money(snapshot.refundable_amount) }}
      </p>
      <p v-if="!snapshot.payment_verified" class="ops-subtle">
        暂无可靠收款凭据，不能按应收金额推定已付款。
      </p>
      <button
        v-if="
          snapshot.payment_verified &&
          Number(snapshot.refundable_amount) > 0 &&
          !snapshot.items.some((row) => row.status === 'open')
        "
        class="ops-button"
        :disabled="busy"
        @click="creating = !creating"
      >
        新建售后申请
      </button>
      <form v-if="creating" class="business-form" @submit.prevent="create">
        <label
          >售后类型<select v-model="draft.kind" :disabled="busy">
            <option value="refund">仅退款</option>
            <option v-if="order.dispatch_started_at" value="return_refund">退货退款</option>
          </select></label
        >
        <label
          >申请退款金额（元）<input
            v-model="draft.amount"
            type="number"
            min="0.01"
            step="0.01"
            :max="snapshot.refundable_amount"
            required
            :disabled="busy"
        /></label>
        <label
          >售后原因<textarea v-model="draft.reason" required maxlength="2000" :disabled="busy" />
        </label>
        <template v-if="draft.kind === 'return_refund'">
          <label v-for="item in order.items" :key="item.id"
            >{{ item.product_name }} · 原购买 {{ item.quantity }} 件<input
              v-model.number="quantities[item.id]"
              type="number"
              min="0"
              :max="item.quantity"
              placeholder="本次退货数量"
              :disabled="busy"
          /></label>
        </template>
        <p class="ops-subtle">创建申请不执行资金退款。处理中售后会暂停未出库订单的交接。</p>
        <button class="ops-button primary" :disabled="busy">保存申请</button>
      </form>
      <article v-for="row in snapshot.items" :key="row.id" class="business-record">
        <h4>
          售后 #{{ row.id }} · {{ row.kind === 'refund' ? '仅退款' : '退货退款' }} ·
          {{ names[row.status] }}
        </h4>
        <p>{{ row.reason }} · 申请 {{ money(row.requested_amount) }}</p>
        <ul v-if="row.refunds.length">
          <li v-for="entry in row.refunds" :key="entry.id">
            退款 {{ money(entry.amount) }} · {{ dateTime(entry.occurred_at) }} · 凭据
            {{ entry.reference }} · {{ entry.recorded_by }}
          </li>
        </ul>
        <ul v-if="row.items.length">
          <li v-for="line in row.items" :key="line.order_item_id">
            {{ line.product_name }} × {{ line.quantity
            }}<span v-if="row.inspected_at">
              · 合格 {{ line.accepted_quantity }} / 损坏 {{ line.damaged_quantity }}</span
            >
          </li>
        </ul>
        <p v-if="row.inspected_at">
          {{ row.inspected_by }} 于 {{ dateTime(row.inspected_at) }} 验收：{{ row.inspection_note }}
        </p>
        <div v-if="row.status === 'open'" class="business-actions">
          <button
            class="ops-button"
            :disabled="
              busy ||
              row.refunds.reduce((sum, item) => sum + Math.round(Number(item.amount) * 100), 0) >=
                Math.round(Number(row.requested_amount) * 100)
            "
            @click="openRefund(row)"
          >
            登记实际退款
          </button>
          <button
            v-if="row.kind === 'return_refund' && !row.inspected_at"
            class="ops-button"
            :disabled="busy"
            @click="openInspection(row)"
          >
            验收退件
          </button>
          <button
            v-if="!row.refunds.length && !row.inspected_at"
            class="ops-button"
            :disabled="busy"
            @click="
              mutate(() => cancelAfterSale(order.id, row.id, row.revision), '售后申请已撤销。')
            "
          >
            撤销申请
          </button>
        </div>
        <form v-if="refundCase?.id === row.id" class="business-form" @submit.prevent="recordRefund">
          <p>请在外部完成退款并核实凭据后登记。此操作不会调用支付机构。</p>
          <label
            >本次实际退款（元）<input
              v-model="refund.amount"
              type="number"
              min="0.01"
              step="0.01"
              required
              :disabled="busy"
          /></label>
          <label
            >凭据 / 交易流水号<input
              v-model="refund.reference"
              required
              maxlength="250"
              :disabled="busy"
          /></label>
          <label
            >实际退款时间<input
              v-model="refund.occurredAt"
              type="datetime-local"
              step="0.001"
              required
              :disabled="busy"
          /></label>
          <button class="ops-button primary" :disabled="busy">核实并保存</button>
        </form>
        <form v-if="inspectionCase?.id === row.id" class="business-form" @submit.prevent="receive">
          <p>逐项核对实物，合格件和损坏件合计应等于本次退货数量。</p>
          <div v-for="line in row.items" :key="line.order_item_id" class="business-actions">
            <span>{{ line.product_name }} × {{ line.quantity }}</span
            ><label
              >合格<input
                v-model.number="inspection[line.order_item_id]!.accepted"
                type="number"
                min="0"
                :max="line.quantity"
                required
                :disabled="busy" /></label
            ><label
              >损坏<input
                v-model.number="inspection[line.order_item_id]!.damaged"
                type="number"
                min="0"
                :max="line.quantity"
                required
                :disabled="busy"
            /></label>
          </div>
          <label
            >验收说明<textarea
              v-model="inspectionNote"
              required
              maxlength="2000"
              :disabled="busy"
            />
          </label>
          <button class="ops-button primary" :disabled="busy">确认验收并登记库存</button>
        </form>
      </article>
    </template>
  </section>
</template>
