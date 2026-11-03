<script setup lang="ts">
// 库存纠错独立于补货：先冻结预览，再由用户确认差额，不提供直接改余额的入口。
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import AppPagination from '../../../shared/components/AppPagination.vue'
import ProductSKUSelect from '../../products/components/ProductSKUSelect.vue'
import { dateTime } from '../../../shared/commerce'
import {
  cancelAdjustment,
  confirmAdjustment,
  createAdjustment,
  fetchAdjustments,
} from '../api/adjustmentsApi'
import type { StockAdjustment } from '../model/adjustments'

const emit = defineEmits<{ changed: [] }>()
const items = ref<StockAdjustment[]>([])
const total = ref(0)
const page = ref(1)
const error = ref('')
const busy = ref(false)
const expanded = ref(false)
const creating = ref(false)
const draft = reactive({ kind: 'count' as StockAdjustment['kind'], reason: '', sourceId: '' })
const lines = ref<{ productId: string; skuId?: number | null; quantity: string }[]>([
  { productId: '', quantity: '' },
])
const kinds = { count: '库存盘点', damage: '报损', correction: '入库更正' }
const statuses = { draft: '待确认草稿', confirmed: '已确认', cancelled: '已作废' }
let alive = true
let generation = 0
let submission = { hash: '', key: '' }

async function load(nextPage = page.value) {
  const sequence = ++generation
  try {
    const result = await fetchAdjustments(nextPage)
    if (alive && sequence === generation) {
      items.value = result.items
      total.value = result.total
      page.value = nextPage
    }
  } catch (reason) {
    if (alive && sequence === generation)
      error.value = reason instanceof Error ? reason.message : '调整单读取失败'
  }
}
async function mutate(action: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await action()
    if (!alive) return
    creating.value = false
    emit('changed')
    await load()
    return true
  } catch (reason) {
    // 版本冲突时保留草稿和差异说明，用户应作废后重新盘点。
    if (alive) error.value = reason instanceof Error ? reason.message : '库存调整失败'
  } finally {
    if (alive) busy.value = false
  }
}
async function save() {
  // 空字符串不能经 Number('') 变成 0，盘点数量也不能用小数冒充整件商品。
  if (
    lines.value.some(
      (line) =>
        !String(line.productId).trim() ||
        !String(line.quantity).trim() ||
        !Number.isInteger(Number(line.productId)) ||
        Number(line.productId) <= 0 ||
        !Number.isInteger(Number(line.quantity)),
    )
  ) {
    error.value = '请填写有效的商品 ID 和整数数量'
    return
  }
  const body = {
    kind: draft.kind,
    reason: draft.reason,
    source_inbound_id: draft.kind === 'correction' ? Number(draft.sourceId) : null,
    items: lines.value.map((line) => ({
      product_id: Number(line.productId),
      ...(line.skuId != null ? { sku_id: line.skuId } : {}),
      ...(draft.kind === 'count'
        ? { counted_on_hand: Number(line.quantity) }
        : { available_delta: Number(line.quantity) }),
    })),
  }
  const hash = JSON.stringify(body)
  if (submission.hash !== hash) submission = { hash, key: crypto.randomUUID() }
  if (await mutate(() => createAdjustment({ ...body, idempotency_key: submission.key })))
    submission = { hash: '', key: '' }
}
onMounted(load)
onBeforeUnmount(() => {
  alive = false
  generation++
})
</script>

<template>
  <section class="business-panel" aria-label="盘点报损与入库更正">
    <header class="fulfillment-card-title">
      <h2>盘点、报损与入库更正</h2>
      <button class="ops-button" :disabled="busy" @click="expanded = !expanded">
        {{ expanded ? '收起' : '展开管理' }} · {{ total }} 单
      </button>
    </header>
    <p class="ops-subtle">
      实盘在库 = 可售 + 有效预留；报损只扣可售数量，已预留商品需先核对关联订单。
    </p>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load()">重读记录</button>
    </p>
    <template v-if="expanded">
      <button class="ops-button" :disabled="busy" @click="creating = !creating">
        新建调整草稿
      </button>
      <form v-if="creating" class="business-form" @submit.prevent="save">
        <label
          >调整类型<select v-model="draft.kind" :disabled="busy">
            <option value="count">库存盘点</option>
            <option value="damage">报损</option>
            <option value="correction">入库更正</option>
          </select></label
        >
        <label v-if="draft.kind === 'correction'"
          >原入库单 ID<input
            v-model="draft.sourceId"
            type="number"
            min="1"
            required
            :disabled="busy"
        /></label>
        <label
          >原因<textarea v-model="draft.reason" required maxlength="2000" :disabled="busy" />
        </label>
        <div v-for="(line, index) in lines" :key="index" class="business-actions">
          <ProductSKUSelect
            v-if="Number(line.productId) > 0"
            v-model="line.skuId"
            :product-id="Number(line.productId) || null"
            :disabled="busy"
          />
          <label
            >商品 ID<input v-model="line.productId" type="number" min="1" required :disabled="busy"
          /></label>
          <label
            >{{ draft.kind === 'count' ? '实盘在库件数（含预留）' : '库存差额（减少填负数）'
            }}<input
              v-model="line.quantity"
              type="number"
              step="1"
              :min="draft.kind === 'count' ? 0 : -1000000"
              :max="draft.kind === 'damage' ? -1 : 2147483647"
              required
              :disabled="busy"
          /></label>
          <button
            v-if="lines.length > 1"
            type="button"
            class="ops-button"
            :disabled="busy"
            @click="lines.splice(index, 1)"
          >
            移除
          </button>
        </div>
        <div class="business-actions">
          <button
            type="button"
            class="ops-button"
            :disabled="busy || lines.length >= 100"
            @click="lines.push({ productId: '', quantity: '' })"
          >
            增加商品行</button
          ><button class="ops-button primary" :disabled="busy">保存差异预览</button>
        </div>
      </form>
      <article v-for="row in items" :key="row.id" class="business-record">
        <h3>ADJ-{{ row.id }} · {{ kinds[row.kind] }} · {{ statuses[row.status] }}</h3>
        <p>
          {{ row.reason }}
          <span v-if="row.source_inbound_id">· 原入库单 #{{ row.source_inbound_id }}</span>
        </p>
        <p class="ops-subtle">
          {{ row.created_by }} · {{ dateTime(row.created_at)
          }}<span v-if="row.confirmed_at">
            · {{ row.confirmed_by }} 于 {{ dateTime(row.confirmed_at) }} 确认</span
          >
        </p>
        <ul>
          <li v-for="line in row.items" :key="line.product_id">
            #{{ line.product_id }} {{ line.product_name }}：可售 {{ line.available_before }} →
            {{ line.available_before + line.available_delta }}；预留
            {{ line.reserved_before }}；差额 {{ line.available_delta > 0 ? '+' : ''
            }}{{ line.available_delta
            }}<span v-if="line.counted_on_hand !== null"
              >；实盘在库 {{ line.counted_on_hand }}</span
            >
          </li>
        </ul>
        <div v-if="row.status === 'draft'" class="business-actions">
          <button
            class="ops-button primary"
            :disabled="busy"
            @click="mutate(() => confirmAdjustment(row.id))"
          >
            确认差额并入账</button
          ><button
            class="ops-button"
            :disabled="busy"
            @click="mutate(() => cancelAdjustment(row.id))"
          >
            作废草稿
          </button>
        </div>
      </article>
      <AppPagination
        v-if="total > 20"
        :page="page"
        :page-size="20"
        :page-count="Math.ceil(total / 20)"
        :total="total"
        :loading="busy"
        @change="load"
      />
    </template>
  </section>
</template>
