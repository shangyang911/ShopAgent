<script setup lang="ts">
// 包裹面板负责多运单、电子面单和实际运费；这些登记动作与订单实物交接出库分别处理。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import type { OrderRecord } from '../../orders/model/types'
import { fetchPackages } from '../api/parcelsApi'
import { request } from '../../../shared/api/httpClient'
import type { LabelTask, PackageSnapshot, ParcelRecord } from '../model/parcels'
import { dateTime, money } from '../../../shared/commerce'

const props = defineProps<{ order: OrderRecord; disabled?: boolean }>()
const emit = defineEmits<{ changed: []; settled: [] }>()
const data = ref<PackageSnapshot | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const configured = ref(false)
const draft = reactive({
  package_no: 2,
  carrier_name: '顺丰速运',
  tracking_number: '',
  remark: '',
  weight: '1.000',
})
const costs = reactive<Record<number, { cost: string; source: string }>>({})
const resolution = reactive({
  task_id: 0,
  outcome: 'issued',
  tracking_number: '',
  label_url: '',
  reason: '',
})
let labelKey = crypto.randomUUID()
let labelHash = ''
let disposed = false
const pending = computed(() =>
  data.value?.label_tasks.some((t) => t.status === 'requesting' || t.status === 'unknown'),
)
const editable = computed(
  () =>
    props.order.status === 'paid_pending_shipment' &&
    !pending.value &&
    !props.disabled &&
    !busy.value,
)
const nextNo = computed(() =>
  data.value?.items.length ? Math.max(...data.value.items.map((p) => p.package_no)) + 1 : 1,
)
const canSettle = computed(
  () =>
    !!props.order.shipment &&
    (!!props.order.shipment.dispatched_at || props.order.shipment.historical),
)
const labelNames = {
  requesting: '申请中',
  unknown: '结果待核实',
  issued: '已取号',
  failed: '申请未成功',
}

async function load() {
  try {
    const result = await fetchPackages(props.order.id)
    if (disposed) return
    data.value = result
    draft.package_no = nextNo.value
    for (const row of data.value.items)
      costs[row.package_no] = {
        cost: row.freight?.actual_cost ?? '',
        source: row.freight?.source ?? '',
      }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '包裹加载失败'
  }
}
async function run(action: () => Promise<unknown>, changed = false) {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    await action()
    await load()
    if (changed && !disposed) emit('changed')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '操作失败，请核对后重试'
  } finally {
    busy.value = false
  }
}
async function saveExtra() {
  if (!editable.value || !data.value) return
  await run(
    () =>
      request(`/shipping/orders/${props.order.id}/packages/${draft.package_no}`, 'PUT', {
        carrier_name: draft.carrier_name,
        tracking_number: draft.tracking_number,
        remark: draft.remark,
        expected_revision: data.value!.revision,
      }),
    true,
  )
}
async function takeLabel() {
  if (!editable.value || !data.value || !configured.value) return
  const no = data.value.items.length ? draft.package_no : 1
  if (
    !window.confirm(
      `为包裹 ${no} 向 ${draft.carrier_name} 申请真实电子面单？可能消耗面单额度。取号后仍需交接出库。`,
    )
  )
    return
  const body = {
    carrier_name: draft.carrier_name,
    weight: draft.weight,
    expected_revision: data.value.revision,
  }
  const hash = JSON.stringify({ no, ...body })
  if (hash !== labelHash) {
    labelHash = hash
    labelKey = crypto.randomUUID()
  }
  await run(async () => {
    const task = await request<LabelTask>(
      `/shipping/orders/${props.order.id}/packages/${no}/label`,
      'POST',
      { ...body, idempotency_key: labelKey },
    )
    notice.value =
      task.status === 'issued'
        ? '已取得电子面单，可打开后打印。'
        : task.error || '申请已登记，请刷新核对任务结果。'
    if (task.status === 'failed') {
      labelKey = crypto.randomUUID()
      labelHash = ''
    }
  }, true)
}
function edit(row: ParcelRecord) {
  Object.assign(draft, {
    package_no: row.package_no,
    carrier_name: row.carrier_name,
    tracking_number: row.tracking_number,
    remark: row.remark,
  })
}
async function remove(row: ParcelRecord) {
  if (!editable.value || !data.value || !window.confirm(`移除未发货包裹 ${row.package_no}？`))
    return
  await run(
    () =>
      request(
        `/shipping/orders/${props.order.id}/packages/${row.package_no}?expected_revision=${data.value!.revision}`,
        'DELETE',
      ),
    true,
  )
}
async function settle(row: ParcelRecord) {
  const values = costs[row.package_no]
  if (!values || !canSettle.value) return
  await run(async () => {
    await request(`/shipping/orders/${props.order.id}/packages/${row.package_no}/freight`, 'PUT', {
      actual_cost: values.cost,
      source: values.source,
      expected_revision: row.freight?.revision ?? 0,
    })
    notice.value = '实际运费已登记，订单应收金额保持原值。'
    if (!disposed) emit('settled')
  })
}
function startResolve(task: LabelTask) {
  Object.assign(resolution, {
    task_id: task.id,
    outcome: 'issued',
    tracking_number: task.tracking_number || '',
    label_url: task.label_url || '',
    reason: '',
  })
}
async function resolveTask() {
  await run(
    () =>
      request(
        `/shipping/orders/${props.order.id}/label-tasks/${resolution.task_id}/resolve`,
        'POST',
        resolution,
      ),
    true,
  )
}
onMounted(async () => {
  await load()
  try {
    configured.value = (
      await request<{ configured: boolean }>('/shipping/label-settings')
    ).configured
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '面单配置加载失败'
  }
})
onBeforeUnmount(() => {
  disposed = true
})
</script>

<template>
  <section class="parcel-panel" :aria-busy="busy" aria-label="包裹与实际运费">
    <h3>包裹与实际运费</h3>
    <p class="ops-subtle">多包裹统一交接，全单商品只出库一次。包裹 1 的物流轨迹可在下方查询。</p>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load">刷新包裹</button>
    </p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="data">
      <p>
        共 {{ data.items.length }} 个包裹 · 已登记实际运费 {{ money(data.actual_cost) }} ·
        {{ data.unsettled_packages }} 个待结算
      </p>
      <article v-for="row in data.items" :key="row.package_no" class="parcel-card">
        <strong>包裹 {{ row.package_no }} · {{ row.carrier_name }}</strong>
        <p>{{ row.tracking_number }}</p>
        <p v-if="row.remark" class="ops-subtle">{{ row.remark }}</p>
        <a
          v-if="row.label?.status === 'issued' && row.label.label_url"
          class="text-action"
          :href="row.label.label_url"
          target="_blank"
          rel="noopener noreferrer"
          >打开电子面单并打印</a
        >
        <div v-if="editable && row.package_no > 1 && !row.label">
          <button class="text-action" @click="edit(row)">编辑包裹</button> ·
          <button class="text-action" @click="remove(row)">移除包裹</button>
        </div>
        <p v-if="row.freight" class="ops-subtle">
          结算登记：{{ row.freight.updated_by }} · {{ dateTime(row.freight.updated_at) }}
        </p>
        <form
          v-if="canSettle && costs[row.package_no]"
          class="fulfillment-form"
          @submit.prevent="settle(row)"
        >
          <label
            >实际运费（元）<input
              v-model="costs[row.package_no]!.cost"
              type="number"
              min="0"
              max="99999999.99"
              step="0.01"
              required
              :disabled="busy"
          /></label>
          <label
            >结算来源 / 修改说明<input
              v-model="costs[row.package_no]!.source"
              maxlength="250"
              placeholder="例如：快递月结账单编号"
              required
              :disabled="busy"
          /></label>
          <button class="ops-button" :disabled="busy">
            {{ row.freight ? '更新实际运费' : '登记实际运费' }}
          </button>
        </form>
      </article>
      <form
        v-if="order.status === 'paid_pending_shipment'"
        class="fulfillment-form"
        @submit.prevent="saveExtra"
      >
        <h4>{{ data.items.length ? '新增或修改包裹' : '申请一号包裹电子面单' }}</h4>
        <fieldset :disabled="!editable">
          <label v-if="data.items.length"
            >包裹序号<input
              v-model.number="draft.package_no"
              type="number"
              min="2"
              max="100"
              required
          /></label>
          <label
            >快递公司<input
              v-model="draft.carrier_name"
              list="fulfillment-carriers"
              maxlength="80"
              required
          /></label>
          <template v-if="data.items.length">
            <label
              >人工登记运单号<input
                v-model="draft.tracking_number"
                minlength="6"
                maxlength="80"
                placeholder="已有运单时填写"
            /></label>
            <label>包裹备注<input v-model="draft.remark" maxlength="1000" /></label>
            <button class="ops-button" type="submit">保存包裹</button>
          </template>
          <label
            >包裹重量（kg）<input
              v-model="draft.weight"
              type="number"
              min="0.001"
              max="1000"
              step="0.001"
          /></label>
          <button class="ops-button" type="button" :disabled="!configured" @click="takeLabel">
            申请电子面单
          </button>
        </fieldset>
        <p v-if="!configured" class="ops-subtle">
          电子面单尚未配置，需设置快递100授权、快递月结账号、模板和寄件地址。
        </p>
        <p v-if="disabled" class="ops-subtle">请先保存上方运单修改。</p>
      </form>
      <div v-for="task in data.label_tasks" :key="task.id" class="parcel-card">
        <p>包裹 {{ task.package_no }} · 面单任务 #{{ task.id }} · {{ labelNames[task.status] }}</p>
        <p v-if="task.error" class="ops-subtle">{{ task.error }}</p>
        <button
          v-if="task.status === 'requesting' || task.status === 'unknown'"
          class="ops-button"
          :disabled="busy"
          @click="startResolve(task)"
        >
          核实平台取号结果
        </button>
      </div>
      <form v-if="resolution.task_id" class="fulfillment-form" @submit.prevent="resolveTask">
        <h4>面单任务 #{{ resolution.task_id }} 核实</h4>
        <p class="ops-subtle">先到快递100后台查明该任务是否取号，再填写核实依据。</p>
        <label
          >平台核实结果<select v-model="resolution.outcome">
            <option value="issued">已取号</option>
            <option value="not_issued">确认未取号</option>
          </select></label
        >
        <template v-if="resolution.outcome === 'issued'">
          <label
            >平台运单号<input
              v-model="resolution.tracking_number"
              minlength="6"
              maxlength="80"
              required
          /></label>
          <label>平台面单链接<input v-model="resolution.label_url" type="url" required /></label>
        </template>
        <label>核实依据<input v-model="resolution.reason" maxlength="500" required /></label>
        <button class="ops-button" :disabled="busy">提交核实结果</button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.parcel-panel {
  border-top: 1px solid var(--border, #e4e7ec);
  margin-top: 24px;
  padding-top: 16px;
}
.parcel-card {
  border: 1px solid var(--border, #e4e7ec);
  padding: 14px;
  border-radius: 10px;
  margin: 12px 0;
  overflow-wrap: anywhere;
}
</style>
