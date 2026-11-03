<script setup lang="ts">
// [AI运行面板] 展示当前账号的请求指标、调用轨迹和反馈；统计口径由服务端统一计算。
import { onMounted, onBeforeUnmount, ref } from 'vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import { request } from '../shared/api/httpClient'
import { dateTime } from '../shared/commerce'

defineProps<{ username: string }>()
interface Summary {
  // 已知用量/费用同时携带覆盖条数，避免把部分已知数据误解为全部消耗。
  total_in_period: number
  sampled_requests: number
  sample_limit: number
  completed_rate: number | null
  tool_success_rate: number | null
  tool_calls: number
  model_calls: number
  answer_p50_ms: number | null
  answer_p95_ms: number | null
  known_tokens: number
  usage_known_requests: number
  known_cost: string
  cost_known_requests: number
  statuses: Record<string, number>
  errors: Record<string, number>
  feedback: Record<string, number>
  abstentions: number
  extractive_fallbacks: number
  replayed_requests: number
}
interface Trace {
  id: string
  started_at: string
  status: string
  question: string
  model: string
  answer_latency_ms: number | null
  duration_ms: number
  error_code: string | null
  grounding: string | null
  feedback: string | null
  replayed: boolean
  answer?: string
  prompt_hash?: string
  tools_hash?: string
  events?: {
    kind: string
    name?: string
    status: string
    latency_ms: number
    arguments?: Record<string, unknown>
    usage?: Record<string, number> | null
  }[]
  token_usage?: Record<string, number> | null
  cost?: string | null
  feedback_note?: string | null
}
const days = ref(7)
const status = ref('')
const feedbackFilter = ref('')
const page = ref(1)
const total = ref(0)
const summary = ref<Summary | null>(null)
const rows = ref<Trace[]>([])
const selected = ref<Trace | null>(null)
const note = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
let generation = 0
// 列表与详情使用不同请求序号，快速筛选或切换轨迹时丢弃旧响应。
let detailGeneration = 0
const labels: Record<string, string> = {
  running: '处理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  helpful: '有帮助',
  unhelpful: '需改进',
}
const percent = (value: number | null) => (value == null ? '—' : `${(value * 100).toFixed(1)}%`)
const ms = (value: number | null) => (value == null ? '—' : `${Math.round(value)} ms`)
function changePage(delta: number) {
  page.value += delta
  void load()
}
async function load(reset = false) {
  // 概览只受日期范围影响；状态和反馈条件只筛选下方请求列表。
  if (reset) page.value = 1
  const id = ++generation
  loading.value = true
  error.value = ''
  try {
    const [overview, list] = await Promise.all([
      request<Summary>(`/agent/observability?days=${days.value}`),
      request<{ items: Trace[]; total: number }>(
        `/agent/observability/traces?days=${days.value}&page=${page.value}&status=${status.value}&feedback=${feedbackFilter.value}`,
      ),
    ])
    if (id !== generation) return
    summary.value = overview
    rows.value = list.items
    total.value = list.total
  } catch (reason) {
    if (id === generation) error.value = reason instanceof Error ? reason.message : '读取失败'
  } finally {
    if (id === generation) loading.value = false
  }
}
async function open(id: string) {
  // 清空旧详情后再加载，防止请求失败时仍把上一条内容当作当前记录。
  const generation = ++detailGeneration
  selected.value = null
  error.value = ''
  try {
    const result = await request<Trace>(`/agent/observability/traces/${encodeURIComponent(id)}`)
    if (generation === detailGeneration) {
      selected.value = result
      note.value = result.feedback_note || ''
    }
  } catch (reason) {
    if (generation === detailGeneration)
      error.value = reason instanceof Error ? reason.message : '读取失败'
  }
}
async function feedback(rating: string) {
  // 保存当前轨迹ID，反馈成功后重新读取已持久化内容，避免只在页面上显示成功。
  if (!selected.value || busy.value) return
  busy.value = true
  error.value = ''
  const id = selected.value.id
  try {
    await request(`/agent/observability/traces/${id}/feedback`, 'PUT', { rating, note: note.value })
    await load()
    await open(id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '反馈失败'
  } finally {
    busy.value = false
  }
}
async function exportCase() {
  // 导出脱敏观察样例供人工复评，不自动重放原请求或执行订单写操作。
  if (!selected.value || busy.value) return
  busy.value = true
  try {
    const data = await request(`/agent/observability/traces/${selected.value.id}/evaluation-case`)
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `agent-case-${selected.value.id}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  } finally {
    busy.value = false
  }
}
onMounted(() => load())
onBeforeUnmount(() => {
  generation++
  detailGeneration++
})
</script>
<template>
  <OperationsShell active-page="observability" title="AI 运行面板" :username="username">
    <div class="ops-heading">
      <div>
        <p class="ops-eyebrow">AI OBSERVABILITY</p>
        <h1>AI 运行面板</h1>
        <p>查看当前账号的请求、工具轨迹和用户反馈。</p>
      </div>
      <button class="ops-button" :disabled="loading" @click="load()">刷新</button>
    </div>
    <form class="ops-filterbar" @submit.prevent="load(true)">
      <label
        >时间范围<select v-model.number="days">
          <option :value="1">近1天</option>
          <option :value="7">近7天</option>
          <option :value="30">近30天</option>
          <option :value="90">近90天</option>
        </select></label
      ><label
        >请求状态<select v-model="status">
          <option value="">全部</option>
          <option
            v-for="key in ['running', 'completed', 'failed', 'cancelled']"
            :key="key"
            :value="key"
          >
            {{ labels[key] }}
          </option>
        </select></label
      ><label
        >反馈<select v-model="feedbackFilter">
          <option value="">全部</option>
          <option value="helpful">有帮助</option>
          <option value="unhelpful">需改进</option>
        </select></label
      ><button class="ops-button" :disabled="loading">查询</button>
    </form>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在读取运行记录…</p>
    <template v-if="summary">
      <p class="ops-notice">
        统计基于当前账号、所选时间范围内最近 {{ summary.sampled_requests }} 条记录（上限
        {{ summary.sample_limit }}；期间共
        {{ summary.total_in_period }}
        条）。状态/反馈筛选仅作用于下方列表。请求完成不代表答案正确；延迟为服务端开始处理至回答持久化，语音输入包含识别等待，播报不计入回答延迟。
      </p>
      <div class="metric-grid">
        <div class="metric-card">
          <span>请求完成率</span><strong>{{ percent(summary.completed_rate) }}</strong
          ><small>{{ summary.statuses.completed || 0 }} 条完成</small>
        </div>
        <div class="metric-card">
          <span>工具调用成功率</span><strong>{{ percent(summary.tool_success_rate) }}</strong
          ><small>{{ summary.tool_calls }} 次工具调用</small>
        </div>
        <div class="metric-card">
          <span>回答 P50 / P95</span
          ><strong style="font-size: 18px"
            >{{ ms(summary.answer_p50_ms) }} / {{ ms(summary.answer_p95_ms) }}</strong
          >
        </div>
        <div class="metric-card">
          <span>已知 Token</span><strong>{{ summary.known_tokens }}</strong
          ><small
            >{{ summary.usage_known_requests }}/{{ summary.sampled_requests }} 条用量完整</small
          >
        </div>
        <div class="metric-card">
          <span>已知估算费用 CNY</span><strong>{{ summary.known_cost }}</strong
          ><small
            >{{ summary.cost_known_requests }}/{{
              summary.sampled_requests
            }}
            条费用可计算；非供应商账单</small
          >
        </div>
      </div>
      <p class="ops-meta">
        <span
          >模型调用 {{ summary.model_calls }} 次 · 拒答 {{ summary.abstentions }} 次 · 原文降级
          {{ summary.extractive_fallbacks }} 次 · 结果重放 {{ summary.replayed_requests }} 次</span
        ><span
          >有帮助 {{ summary.feedback.helpful || 0 }} · 需改进
          {{ summary.feedback.unhelpful || 0 }}</span
        >
      </p>
      <p v-if="Object.keys(summary.errors).length" class="ops-notice">
        失败类型：<span v-for="(count, code) in summary.errors" :key="code"
          >{{ code }}（{{ count }}）
        </span>
      </p>
    </template>
    <div class="ops-table-wrap">
      <table class="ops-table">
        <thead>
          <tr>
            <th>时间 / 模型</th>
            <th>问题摘要</th>
            <th>状态 / 反馈</th>
            <th>回答耗时</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td>
              {{ dateTime(row.started_at) }}<small>{{ row.model }}</small>
            </td>
            <td>{{ row.question || '无文字输入' }}</td>
            <td>
              {{ labels[row.status] || row.status
              }}<small
                >{{ row.feedback ? labels[row.feedback] : '暂无反馈'
                }}{{ row.replayed ? ' · 重放' : '' }}</small
              ><small v-if="row.error_code">{{ row.error_code }}</small>
            </td>
            <td>{{ ms(row.answer_latency_ms) }}</td>
            <td><button class="ops-button" @click="open(row.id)">查看详情</button></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length && !loading" class="ops-empty">
        尚无匹配记录。新请求开始处理后会记录，历史会话不回填虚构指标。
      </p>
    </div>
    <div class="ops-meta">
      <span>共 {{ total }} 条 · 第 {{ page }} 页</span>
      <div class="section-actions">
        <button class="ops-button" :disabled="loading || page <= 1" @click="changePage(-1)">
          上一页</button
        ><button
          class="ops-button"
          :disabled="loading || page * 20 >= total"
          @click="changePage(1)"
        >
          下一页
        </button>
      </div>
    </div>
    <section v-if="selected" class="ops-panel">
      <h2>请求详情</h2>
      <p style="white-space: pre-wrap">
        问题：{{ selected.question }}<br />回答摘要：{{ selected.answer }}
      </p>
      <p>
        总耗时 {{ ms(selected.duration_ms) }} · 回答耗时 {{ ms(selected.answer_latency_ms) }} ·
        Token {{ selected.token_usage?.total_tokens ?? '未知' }} · 估算费用
        {{ selected.cost ?? '未知' }} CNY
      </p>
      <label class="form-field"
        >反馈说明（最多500字）<textarea v-model="note" maxlength="500" rows="3" :disabled="busy" />
      </label>
      <div class="section-actions">
        <button class="ops-button" :disabled="busy" @click="feedback('helpful')">有帮助</button
        ><button class="ops-button" :disabled="busy" @click="feedback('unhelpful')">需改进</button>
      </div>
      <details :key="selected.id" class="trace-technical-details">
        <summary>技术详情</summary>
        <p style="overflow-wrap: anywhere">
          请求 ID：{{ selected.id }}<br />模型：{{ selected.model }}<br />Prompt：{{
            selected.prompt_hash
          }}<br />Tools：{{ selected.tools_hash }}
        </p>
        <details v-for="(event, index) in selected.events" :key="index">
          <summary>
            {{ index + 1 }}. {{ event.kind }} {{ event.name || '' }} · {{ event.status }} ·
            {{ ms(event.latency_ms) }}
          </summary>
          <pre style="white-space: pre-wrap; overflow-wrap: anywhere">{{
            JSON.stringify(event, null, 2)
          }}</pre>
        </details>
        <button class="ops-button" :disabled="busy" @click="exportCase">导出复评样例</button>
        <p class="ops-subtle">
          导出为待人工核对的脱敏样例，观察结果不作为标准答案；核对上下文和业务夹具后再加入评测集。
        </p>
      </details>
    </section>
  </OperationsShell>
</template>

<style scoped>
.trace-technical-details {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}
.trace-technical-details > summary {
  cursor: pointer;
  color: #637c73;
  font-size: 12px;
}
.trace-technical-details > details {
  margin: 12px 0;
}
.trace-technical-details > button {
  margin-top: 16px;
}
</style>
