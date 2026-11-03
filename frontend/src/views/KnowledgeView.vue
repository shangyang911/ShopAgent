 <script setup lang="ts">
// [知识库管理] 编辑新草稿、核对发布、撤回版本，并验证检索到的原文证据。
import { onMounted, onBeforeUnmount, ref } from 'vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import KnowledgeEvidence from '../features/agent/KnowledgeEvidence.vue'
import type { KnowledgeSource } from '../features/agent/types'
import { request } from '../shared/api/httpClient'

defineProps<{ username: string }>()
interface DocumentVersion {
  id: string
  document_key: string
  version: string
  title: string
  content?: string
  effective_from: string
  effective_until: string | null
  status: string
  currently_effective: boolean
  chunk_count: number
  content_hash: string
  created_by: string
}
const items = ref<DocumentVersion[]>([])
const selected = ref<DocumentVersion | null>(null)
const formOpen = ref(false)
const form = ref({
  document_key: '',
  version: '',
  title: '',
  content: '',
  effective_from: new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(
    new Date(),
  ),
  effective_until: '',
})
const query = ref('')
const results = ref<KnowledgeSource[]>([])
const searchNotice = ref('')
const error = ref('')
const busy = ref(false)
let generation = 0
let searchGeneration = 0
const states: Record<string, string> = { draft: '草稿', published: '已发布', withdrawn: '已撤回' }

async function load() {
  // 请求序号避免旧列表覆盖刚保存或发布后的新状态。
  const id = ++generation
  try {
    const result = await request<{ items: DocumentVersion[] }>('/knowledge')
    if (id === generation) items.value = result.items
  } catch (reason) {
    if (id === generation) error.value = reason instanceof Error ? reason.message : '读取失败'
  }
}
async function open(id: string) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    selected.value = await request<DocumentVersion>(`/knowledge/${encodeURIComponent(id)}`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '读取失败'
  } finally {
    busy.value = false
  }
}
function newVersion(row?: DocumentVersion) {
  // 复用旧版正文但清空版本号；已发布文档通过新版本修订，不直接覆盖历史内容。
  if (busy.value) return
  form.value = {
    document_key: row?.document_key || '',
    version: '',
    title: row?.title || '',
    content: row?.content || '',
    effective_from: new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(
      new Date(),
    ),
    effective_until: '',
  }
  formOpen.value = true
}
async function importText(event: Event) {
  // 文件只在浏览器读取为文本；限制类型和大小，正文交给后端再次校验。
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 200000 || !/\.(md|txt)$/i.test(file.name)) {
    error.value = '请选择不超过200KB的 .md 或 .txt 文件'
    return
  }
  const text = await file.text()
  if (text.length > 50000) {
    error.value = '正文最多50000字符'
    return
  }
  form.value.content = text
}
async function save() {
  // 未填结束日期转换为null；创建接口始终保存草稿，发布需要独立核对操作。
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    selected.value = await request<DocumentVersion>('/knowledge', 'POST', {
      ...form.value,
      effective_until: form.value.effective_until || null,
    })
    formOpen.value = false
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    busy.value = false
  }
}
async function change(operation: 'publish' | 'withdraw') {
  // 状态变更后清空旧检索结果，避免仍展示撤回前或发布前的检索证据。
  if (busy.value || !selected.value) return
  if (
    !window.confirm(
      `${operation === 'publish' ? '发布' : '撤回'}《${selected.value.title}》${selected.value.version}？发布内容不可编辑；撤回后不会自动恢复旧版政策。`,
    )
  )
    return
  busy.value = true
  error.value = ''
  try {
    selected.value = await request<DocumentVersion>(
      `/knowledge/${selected.value.id}/${operation}`,
      'POST',
    )
    results.value = []
    searchNotice.value = '知识状态已变化，请重新检索。'
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '更新失败'
  } finally {
    busy.value = false
  }
}
async function search() {
  // 查询结果是证据片段，不使用v-html渲染原文，避免把文档内容执行为页面代码。
  const id = ++searchGeneration
  results.value = []
  searchNotice.value = '检索中…'
  try {
    const result = await request<{
      documents: (KnowledgeSource & { text: string })[]
      notice: string
    }>(`/knowledge/search?query=${encodeURIComponent(query.value)}`)
    if (id !== searchGeneration) return
    results.value = result.documents.map((d) => ({ ...d, excerpt: d.text }))
    searchNotice.value = result.notice
  } catch (reason) {
    if (id === searchGeneration)
      searchNotice.value = reason instanceof Error ? reason.message : '检索失败'
  }
}
onMounted(async () => {
  await load()
  const id = new URLSearchParams(window.location.search).get('document_id')
  if (id) await open(id)
})
onBeforeUnmount(() => {
  generation++
  searchGeneration++
})
</script>
<template>
  <OperationsShell active-page="knowledge" title="门店知识库" :username="username">
    <div class="ops-heading">
      <div>
        <p class="ops-eyebrow">KNOWLEDGE & EVIDENCE</p>
        <h1>门店知识库</h1>
        <p>版本化政策、适用日期和可核对的引用原文。</p>
      </div>
      <button class="ops-button primary" :disabled="busy" @click="newVersion()">新建知识版本</button
      ><a href="/agent" class="ops-button">返回运营助手</a>
    </div>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p class="ops-notice">
      仅已发布且当前生效的最新版本参与回答。草稿、未来版本、过期或撤回版本不作为当前政策。发布后修改内容须创建新版本。
    </p>
    <section class="ops-panel">
      <h2>检索验证</h2>
      <form class="ops-filterbar" @submit.prevent="search">
        <label class="grow">问题<input v-model="query" required maxlength="500" /></label
        ><button class="ops-button">验证检索</button>
      </form>
      <p role="status">{{ searchNotice }}</p>
      <KnowledgeEvidence v-if="results.length" :sources="results" />
    </section>
    <section v-if="formOpen" class="ops-panel">
      <h2>导入纯文本 / Markdown</h2>
      <form class="product-form" @submit.prevent="save">
        <div class="product-form-grid">
          <label class="form-field"
            >文档标识（同政策沿用同标识）<input
              v-model="form.document_key"
              required
              pattern="[a-zA-Z0-9_-]+"
              maxlength="80"
              :disabled="busy" /></label
          ><label class="form-field"
            >版本号<input v-model="form.version" required maxlength="40" :disabled="busy" /></label
          ><label class="form-field"
            >标题<input v-model="form.title" required maxlength="200" :disabled="busy" /></label
          ><label class="form-field"
            >生效日期<input
              v-model="form.effective_from"
              type="date"
              required
              :disabled="busy" /></label
          ><label class="form-field"
            >失效日期（可选）<input
              v-model="form.effective_until"
              type="date"
              :min="form.effective_from"
              :disabled="busy" /></label
          ><label class="form-field"
            >从文件读取<input type="file" accept=".md,.txt" :disabled="busy" @change="importText"
          /></label>
          <label class="form-field form-field-wide"
            >正文（最多50000字符）<textarea
              v-model="form.content"
              rows="12"
              minlength="10"
              maxlength="50000"
              required
              :disabled="busy"
            />
          </label>
        </div>
        <footer class="product-dialog-footer">
          <button type="button" class="ops-button" :disabled="busy" @click="formOpen = false">
            收起</button
          ><button class="ops-button primary" :disabled="busy">保存为草稿</button>
        </footer>
      </form>
    </section>
    <div class="ops-table-wrap">
      <table class="ops-table">
        <thead>
          <tr>
            <th>文档 / 版本</th>
            <th>状态</th>
            <th>有效期</th>
            <th>片段数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.id">
            <td>
              {{ row.title }}<small>{{ row.document_key }} · {{ row.version }}</small>
            </td>
            <td>
              {{ states[row.status]
              }}<small>{{ row.currently_effective ? '当前适用' : '不参与当前检索' }}</small>
            </td>
            <td>{{ row.effective_from }} 至 {{ row.effective_until || '长期' }}</td>
            <td>{{ row.chunk_count }}</td>
            <td>
              <button class="ops-button" :disabled="busy" @click="open(row.id)">查看原文</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!items.length" class="ops-empty">尚无知识文档。请导入经核实的门店政策。</p>
    </div>
    <section v-if="selected" class="ops-panel">
      <h2>{{ selected.title }} · {{ selected.version }}</h2>
      <p>
        {{ states[selected.status] }} ·
        {{ selected.currently_effective ? '当前适用' : '非当前适用版本' }} ·
        {{ selected.effective_from }} 至 {{ selected.effective_until || '长期' }}
      </p>
      <pre style="white-space: pre-wrap; overflow-wrap: anywhere">{{ selected.content }}</pre>
      <p style="overflow-wrap: anywhere">内容指纹：{{ selected.content_hash }}</p>
      <div class="section-actions">
        <button class="ops-button" :disabled="busy" @click="newVersion(selected)">
          以此创建新版本</button
        ><button
          v-if="selected.status === 'draft'"
          class="ops-button primary"
          :disabled="busy"
          @click="change('publish')"
        >
          核对并发布</button
        ><button
          v-if="selected.status === 'published'"
          class="ops-button"
          :disabled="busy"
          @click="change('withdraw')"
        >
          撤回版本
        </button>
      </div>
    </section>
  </OperationsShell>
</template>
