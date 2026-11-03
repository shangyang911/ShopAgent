<script setup lang="ts">
// [运营建议工作台] 展示后端计算依据，核对后创建草稿；实际入库仍走仓库确认流程。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { request } from '../../../shared/api/httpClient'
import { query } from '../../../shared/api/query'

interface PlanRow {
  product_id: number
  sku_id: number
  name: string
  sku_code: string
  label: string
  available: number
  reserved: number
  paid_units_7d: number
  paid_units_30d: number
  daily_demand: number
  coverage_days: number | null
  target: number
  draft_quantity: number
  suggested_quantity: number
  confidence: string
  basis: string
}
interface Parameters {
  keyword: string
  coverage_days: number
  safety_percent: number
  minimum_stock: number
}
interface Plan {
  generated_at: string
  parameters: Parameters
  items: PlanRow[]
  notice: string
  formula: string
  truncated: boolean
}
interface Shipping {
  notice: string
  truncated: boolean
  items: {
    order_id: number
    order_number: string
    waiting_hours: number | null
    overdue: boolean
    reasons: string[]
    href: string
  }[]
}
const emit = defineEmits<{ created: [id: number] }>()
const initial = new URLSearchParams(window.location.search)
const mode = ref(initial.get('analysis') === 'shipping' ? 'shipping' : 'replenishment')
const bounded = (key: string, fallback: number, min: number, max: number) => {
  // 从Agent跳转链接恢复参数时先校验边界，非法URL参数回退默认值。
  const n = Number(initial.get(key) ?? fallback)
  return Number.isInteger(n) && n >= min && n <= max ? n : fallback
}
const parameters = ref<Parameters>({
  keyword: initial.get('keyword') || '',
  coverage_days: bounded('coverage_days', 7, 1, 90),
  safety_percent: bounded('safety_percent', 15, 0, 100),
  minimum_stock: bounded('minimum_stock', 5, 0, 10000),
})
const plan = ref<Plan | null>(null)
const shipping = ref<Shipping | null>(null)
const selected = ref<number[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
let generation = 0
let intentKey = ''
let intentSignature = ''
const picked = computed(
  () =>
    plan.value?.items.filter(
      (r) => selected.value.includes(r.sku_id) && r.suggested_quantity > 0,
    ) ?? [],
)
const unchanged = computed(
  // 页面参数变更后旧建议立即不可提交，必须重新分析以取得对应来源摘要。
  () => !!plan.value && JSON.stringify(parameters.value) === JSON.stringify(plan.value.parameters),
)

async function analyze() {
  // 分析与保存互斥；切换模式时清空旧建议和选择，旧请求晚到也不能恢复过期结果。
  if (saving.value) return
  window.history.replaceState(
    null,
    '',
    `/inventory?${query({ tab: 'copilot', analysis: mode.value, ...parameters.value })}`,
  )
  const id = ++generation
  loading.value = true
  error.value = notice.value = ''
  selected.value = []
  plan.value = null
  shipping.value = null
  try {
    if (mode.value === 'shipping') {
      const result = await request<Shipping>('/copilot/shipping-analysis')
      if (id === generation) shipping.value = result
    } else {
      const result = await request<Plan>(`/copilot/replenishment?${query({ ...parameters.value })}`)
      if (id === generation) plan.value = result
    }
  } catch (reason) {
    if (id === generation) error.value = reason instanceof Error ? reason.message : '分析失败'
  } finally {
    if (id === generation) loading.value = false
  }
}
function changeMode(value: string) {
  mode.value = value
  void analyze()
}
async function createDraft() {
  if (loading.value || saving.value || !unchanged.value || !plan.value || !picked.value.length)
    return
  if (
    !window.confirm(
      `已核对 ${picked.value.length} 个SKU，共 ${picked.value.reduce((n, r) => n + r.suggested_quantity, 0)} 件，创建入库草稿？草稿不会增加库存，实际到货仍需仓库确认。`,
    )
  )
    return
  const body = {
    parameters: plan.value.parameters,
    items: picked.value.map((r) => ({
      product_id: r.product_id,
      sku_id: r.sku_id,
      quantity: r.suggested_quantity,
      basis: r.basis,
    })),
  }
  const signature = JSON.stringify(body)
  // 相同提交内容复用幂等键，响应丢失后重试不会生成第二张草稿。
  if (signature !== intentSignature) {
    intentSignature = signature
    intentKey = crypto.randomUUID()
  }
  saving.value = true
  error.value = ''
  try {
    const result = await request<{ id: number; receipt_number: string }>(
      '/copilot/replenishment/draft',
      'POST',
      { ...body, idempotency_key: intentKey },
    )
    selected.value = []
    plan.value = null
    // 创建草稿后清除建议，等待父页面打开既有入库草稿核对，不在这里增加库存。
    notice.value = `草稿 ${result.receipt_number} 已创建，库存尚未增加。`
    emit('created', result.id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '草稿创建失败'
  } finally {
    saving.value = false
  }
}
onMounted(analyze)
onBeforeUnmount(() => {
  generation++
})
</script>

<template>
  <section class="ops-panel" aria-label="运营 Copilot">
    <div class="ops-heading">
      <div>
        <p class="ops-eyebrow">OPERATIONS COPILOT</p>
        <h2>运营建议</h2>
        <p>以实际业务数据计算，核对后再执行。</p>
      </div>
      <div class="ops-segments">
        <button
          :class="{ active: mode === 'replenishment' }"
          :disabled="saving || loading"
          @click="changeMode('replenishment')"
        >
          智能补货
        </button>
        <button
          :class="{ active: mode === 'shipping' }"
          :disabled="saving || loading"
          @click="changeMode('shipping')"
        >
          待发货分析
        </button>
      </div>
    </div>
    <form v-if="mode === 'replenishment'" class="ops-filterbar" @submit.prevent="analyze">
      <label
        >商品名称<input v-model="parameters.keyword" maxlength="180" :disabled="saving"
      /></label>
      <label
        >覆盖未来天数<input
          v-model.number="parameters.coverage_days"
          type="number"
          min="1"
          max="90"
          required
          :disabled="saving"
      /></label>
      <label
        >安全比例 %<input
          v-model.number="parameters.safety_percent"
          type="number"
          min="0"
          max="100"
          required
          :disabled="saving"
      /></label>
      <label
        >每SKU最低备货目标<input
          v-model.number="parameters.minimum_stock"
          type="number"
          min="0"
          max="10000"
          required
          :disabled="saving"
      /></label>
      <button class="ops-button" :disabled="loading || saving">
        {{ loading ? '分析中…' : '重新分析' }}
      </button>
    </form>
    <button v-else class="ops-button" :disabled="loading" @click="analyze">刷新分析</button>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="ops-notice" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">正在读取库存和订单事实…</p>
    <template v-if="plan">
      <p class="ops-notice">{{ plan.notice }}<br />{{ plan.formula }}</p>
      <p v-if="plan.truncated" class="ops-error">仅分析了500个SKU，请缩小商品范围后重新分析。</p>
      <p v-if="!unchanged" class="ops-error">筛选参数已修改，请重新分析后再创建草稿。</p>
      <button
        class="ops-button primary"
        :disabled="saving || loading || !unchanged || !picked.length || picked.length > 100"
        @click="createDraft"
      >
        {{ saving ? '创建中…' : `核对并创建入库草稿（${picked.length}个SKU）` }}
      </button>
      <div class="ops-table-wrap">
        <table class="ops-table">
          <thead>
            <tr>
              <th>选择</th>
              <th>商品 / SKU</th>
              <th>可售 / 预留</th>
              <th>7 / 30天需求</th>
              <th>可售覆盖天数</th>
              <th>目标 / 已有草稿</th>
              <th>新建建议</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in plan.items" :key="row.sku_id">
              <td>
                <input
                  v-model="selected"
                  type="checkbox"
                  :value="row.sku_id"
                  :disabled="saving || !unchanged || row.suggested_quantity === 0"
                  :aria-label="`选择 ${row.name} ${row.label}`"
                />
              </td>
              <td>
                <strong>{{ row.name }}</strong
                ><small>{{ row.label }} · {{ row.sku_code }}</small
                ><small>{{ row.confidence }}</small>
              </td>
              <td>{{ row.available }} / {{ row.reserved }}</td>
              <td>{{ row.paid_units_7d }} / {{ row.paid_units_30d }}</td>
              <td>
                {{ row.coverage_days == null ? '无历史，不预测' : `${row.coverage_days} 天` }}
              </td>
              <td>{{ row.target }} / {{ row.draft_quantity }}</td>
              <td>{{ row.suggested_quantity }} 件</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!plan.items.length" class="ops-empty">当前范围没有可售SKU。</p>
      <p class="ops-subtle">
        每次最多100个SKU；已有草稿可在“补货入库”中核对、作废或按实物到货确认。
      </p>
    </template>
    <template v-if="shipping">
      <p class="ops-notice">{{ shipping.notice }}</p>
      <p v-if="shipping.truncated" class="ops-error">仅分析前500笔，请在发货页继续核对。</p>
      <div class="ops-table-wrap">
        <table class="ops-table">
          <thead>
            <tr>
              <th>订单</th>
              <th>收款后等待</th>
              <th>核对条件</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in shipping.items" :key="row.order_id">
              <td>{{ row.order_number }}</td>
              <td>
                {{ row.waiting_hours == null ? '历史时间未知' : `${row.waiting_hours} 小时`
                }}<small v-if="row.overdue">已超过24小时</small>
              </td>
              <td>{{ row.reasons.join('；') }}</td>
              <td><a :href="row.href">办理发货</a></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!shipping.items.length" class="ops-empty">当前没有已付款待发货订单。</p>
    </template>
  </section>
</template>
