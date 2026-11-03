<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import type { ProductDetail, ProductSKU } from '../model/types'
import {
  saveProductDetail,
  saveProductSKU,
  transferProductStock,
  uploadProductImage,
} from '../api/productDetailsApi'
import { resolveProductImageUrl } from '../api/productsApi'

const props = defineProps<{ detail: ProductDetail }>()
const emit = defineEmits<{ changed: [value: ProductDetail] }>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const revision = ref(1)
const gallery = ref<{ id: number; alt: string; color: string }[]>([])
const description = ref<ProductDetail['description']>([])
const parameters = ref<ProductDetail['parameters']>([])
const skuId = ref<number | undefined>()
const skuRevision = ref<number | undefined>()
const skuForm = ref({ code: '', color: '', size: '', price: '0.00', active: true })
const source = ref<number | null>(null)
const target = ref<number | null>(null)
const transferQuantity = ref(1)
const transferReason = ref('')
let transferHash = ''
let transferKey = ''
let disposed = false
function resetContent() {
  revision.value = props.detail.revision
  gallery.value = props.detail.images
    .filter((image) => image.id != null)
    .map((image) => ({ id: image.id!, alt: image.alt, color: image.color }))
  description.value = props.detail.description.map((block) => ({ ...block }))
  parameters.value = props.detail.parameters.map((parameter) => ({ ...parameter }))
}
watch(() => props.detail.id, resetContent, { immediate: true })
async function run(action: () => Promise<ProductDetail>, message: string) {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    const value = await action()
    if (disposed) return
    emit('changed', value)
    revision.value = value.revision
    notice.value = message
    return value
  } catch (reason) {
    if (!disposed) error.value = reason instanceof Error ? reason.message : '保存失败，请核对后重试'
  } finally {
    if (!disposed) busy.value = false
  }
}
async function saveContent() {
  const value = await run(
    () =>
      saveProductDetail(props.detail.id, {
        expected_revision: revision.value,
        gallery: gallery.value,
        description: description.value.map((block) => ({
          type: block.type,
          text: block.text,
          image_id: block.image_id,
        })),
        parameters: parameters.value,
      }),
    '图文详情和轮播顺序已保存',
  )
  if (value) description.value = value.description.map((block) => ({ ...block }))
}
async function upload(event: Event, role: 'gallery' | 'detail') {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (!files.length || busy.value) return
  if (
    files.some(
      (file) =>
        !['image/jpeg', 'image/png', 'image/webp'].includes(file.type) ||
        file.size > 5 * 1024 * 1024,
    )
  ) {
    error.value = '请选择不超过 5 MB 的 JPG、PNG 或 WebP 图片'
    return
  }
  busy.value = true
  error.value = notice.value = ''
  try {
    let current = props.detail
    for (const file of files) {
      if (disposed) return
      const existing = new Set(current.media.map((image) => image.id))
      const value = await uploadProductImage(props.detail.id, file, revision.value, role)
      if (disposed) return
      revision.value = value.revision
      const added = value.media.filter((image) => !existing.has(image.id))
      current = value
      if (role === 'gallery')
        gallery.value.push(
          ...added
            .filter((image) => image.id != null)
            .map((image) => ({ id: image.id!, alt: image.alt, color: image.color })),
        )
      else
        description.value.push(
          ...added.map((image) => ({
            type: 'image' as const,
            image_id: image.id,
            text: '',
            url: image.url,
          })),
        )
      emit('changed', value)
    }
    notice.value =
      role === 'detail'
        ? '详情图已上传，请保存图文详情以确认位置'
        : '图片已上传，可调整顺序和颜色后保存'
  } catch (reason) {
    if (!disposed)
      error.value = reason instanceof Error ? reason.message : '上传失败，已上传图片会保留'
  } finally {
    if (!disposed) busy.value = false
  }
}
function reorder<T>(rows: T[], index: number, offset: number) {
  const next = index + offset
  if (next < 0 || next >= rows.length) return
  const [value] = rows.splice(index, 1)
  if (value) rows.splice(next, 0, value)
}
function editSKU(sku?: ProductSKU) {
  skuId.value = sku?.id
  skuRevision.value = sku?.revision
  skuForm.value = sku
    ? { code: sku.code, color: sku.color, size: sku.size, price: sku.price, active: sku.active }
    : { code: '', color: '', size: '', price: '0.00', active: true }
}
async function saveSKU() {
  const value = await run(
    () =>
      saveProductSKU(
        props.detail.id,
        { ...skuForm.value, expected_revision: skuRevision.value },
        skuId.value,
      ),
    skuId.value == null ? '新 SKU 已创建，初始库存为 0' : 'SKU 资料已保存，库存未修改',
  )
  if (value) editSKU()
}
async function allocate() {
  if (source.value == null || target.value == null) return
  const body = {
    from_sku_id: source.value,
    to_sku_id: target.value,
    quantity: transferQuantity.value,
    reason: transferReason.value.trim(),
  }
  const hash = JSON.stringify(body)
  if (transferHash !== hash) {
    transferHash = hash
    transferKey = crypto.randomUUID()
  }
  const value = await run(
    () => transferProductStock(props.detail.id, { ...body, idempotency_key: transferKey }),
    'SKU 库存已重新分配，商品总库存不变',
  )
  if (value) {
    transferHash = ''
    transferKey = ''
    transferReason.value = ''
  }
}
onBeforeUnmount(() => {
  disposed = true
})
</script>

<template>
  <section class="product-editor" aria-label="管理商品详情">
    <h2>图片、内容与规格管理</h2>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="editor-notice" role="status">{{ notice }}</p>
    <fieldset :disabled="busy">
      <details open>
        <summary>轮播图片与图文详情</summary>
        <div class="editor-actions">
          <label class="ops-button"
            >上传轮播图<input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              @change="upload($event, 'gallery')" /></label
          ><label class="ops-button"
            >上传详情图<input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              @change="upload($event, 'detail')" /></label
          ><button class="ops-button" @click="resetContent">还原未保存的内容</button>
        </div>
        <p class="ops-subtle">
          轮播最多 20
          张，首张作为商品主图。可为图片标注颜色，选择颜色时会切换到对应图片。每张图片不超过 5 MB。
        </p>
        <div class="editor-gallery">
          <article v-for="(photo, index) in gallery" :key="photo.id">
            <img
              :src="
                resolveProductImageUrl(
                  detail.media.find((image) => image.id === photo.id)?.url || '',
                )
              "
              :alt="photo.alt"
            /><label>图片说明<input v-model="photo.alt" maxlength="180" /></label
            ><label>对应颜色<input v-model="photo.color" maxlength="40" /></label>
            <div class="editor-actions">
              <button
                class="text-action"
                :disabled="index === 0"
                @click="reorder(gallery, index, -1)"
              >
                前移</button
              ><button
                class="text-action"
                :disabled="index === gallery.length - 1"
                @click="reorder(gallery, index, 1)"
              >
                后移</button
              ><button
                class="text-action"
                :disabled="gallery.length <= 1"
                @click="gallery.splice(index, 1)"
              >
                移出轮播
              </button>
            </div>
          </article>
        </div>
        <h3>图文内容</h3>
        <article v-for="(block, index) in description" :key="index" class="editor-block">
          <textarea
            v-if="block.type === 'text'"
            v-model="block.text"
            rows="4"
            maxlength="10000"
            :aria-label="`第 ${index + 1} 段详情文字`"
          /><img v-else-if="block.url" :src="resolveProductImageUrl(block.url)" alt="详情图片" />
          <div class="editor-actions">
            <button
              class="text-action"
              :disabled="index === 0"
              @click="reorder(description, index, -1)"
            >
              上移</button
            ><button
              class="text-action"
              :disabled="index === description.length - 1"
              @click="reorder(description, index, 1)"
            >
              下移</button
            ><button class="text-action" @click="description.splice(index, 1)">移除段落</button>
          </div>
        </article>
        <button
          class="ops-button"
          @click="description.push({ type: 'text', text: '', image_id: null })"
        >
          添加文字段落
        </button>
        <h3>规格参数</h3>
        <div v-for="(parameter, index) in parameters" :key="index" class="editor-parameter">
          <input
            v-model="parameter.name"
            placeholder="参数名，如材质"
            maxlength="80"
            aria-label="参数名"
          /><input
            v-model="parameter.value"
            placeholder="参数值"
            maxlength="500"
            aria-label="参数值"
          /><button class="text-action" @click="parameters.splice(index, 1)">删除</button>
        </div>
        <button class="ops-button" @click="parameters.push({ name: '', value: '' })">
          添加参数
        </button>
        <div class="editor-actions">
          <button class="ops-button primary" @click="saveContent">保存轮播、图文与参数</button>
        </div>
      </details>
      <details>
        <summary>SKU 颜色、尺寸与价格</summary>
        <p class="ops-subtle">
          每种颜色与尺寸组合独立记库存。新建 SKU
          不会增加商品总库存，可通过库存中心补货或在下方分配现有库存。
        </p>
        <div class="editor-skus">
          <button v-for="sku in detail.skus" :key="sku.id" class="ops-button" @click="editSKU(sku)">
            {{ sku.label }} · {{ sku.code }} · 库存 {{ sku.stock }}
          </button>
        </div>
        <form class="editor-form" @submit.prevent="saveSKU">
          <label
            >SKU 编码<input
              v-model="skuForm.code"
              pattern="[A-Za-z0-9_\-]+"
              maxlength="80"
              required /></label
          ><label>颜色<input v-model="skuForm.color" maxlength="40" /></label
          ><label>尺寸<input v-model="skuForm.size" maxlength="40" /></label
          ><label
            >价格<input
              v-model="skuForm.price"
              type="number"
              min="0"
              max="99999999.99"
              step="0.01"
              required /></label
          ><label
            >状态<select v-model="skuForm.active">
              <option :value="true">启用</option>
              <option :value="false">停用（需清空库存和预留）</option>
            </select></label
          ><button class="ops-button primary">
            {{ skuId == null ? '新增 SKU' : '保存所选 SKU' }}</button
          ><button type="button" class="ops-button" @click="editSKU()">清空并新建</button>
        </form>
      </details>
      <details v-if="detail.skus.length > 1">
        <summary>分配现有 SKU 库存</summary>
        <p class="ops-subtle">
          仅在实物规格已核对后分配可售库存，不能挪用已被订单预留的数量。新到货请通过库存中心入库。
        </p>
        <form class="editor-form" @submit.prevent="allocate">
          <label
            >转出规格<select v-model.number="source" required>
              <option :value="null" disabled>请选择</option>
              <option v-for="sku in detail.skus" :key="sku.id" :value="sku.id">
                {{ sku.label }} · 可售 {{ sku.stock }}
              </option>
            </select></label
          ><label
            >转入规格<select v-model.number="target" required>
              <option :value="null" disabled>请选择</option>
              <option
                v-for="sku in detail.skus.filter((row) => row.active)"
                :key="sku.id"
                :value="sku.id"
              >
                {{ sku.label }}
              </option>
            </select></label
          ><label
            >数量<input
              v-model.number="transferQuantity"
              type="number"
              min="1"
              max="1000000"
              step="1"
              required /></label
          ><label>核对说明<input v-model="transferReason" maxlength="2000" required /></label
          ><button
            class="ops-button primary"
            :disabled="source === target || source == null || target == null"
          >
            确认分配库存
          </button>
        </form>
      </details>
    </fieldset>
  </section>
</template>

<style scoped>
.product-editor {
  padding: 22px;
  background: #f5faf7;
  border: 1px solid #d8e9df;
  border-radius: 12px;
  margin-bottom: 24px;
}
.product-editor h2 {
  font-size: 18px;
  color: #315347;
}
.product-editor h3 {
  font-size: 14px;
  margin-top: 24px;
}
.product-editor fieldset {
  border: 0;
  padding: 0;
  min-width: 0;
}
.product-editor details {
  border-top: 1px solid #dce8e0;
  padding: 16px 0;
}
.product-editor summary {
  cursor: pointer;
  font-weight: 600;
  color: #3b6251;
}
.editor-actions,
.editor-skus,
.editor-form {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: end;
  margin: 14px 0;
}
.editor-actions input[type='file'] {
  max-width: 180px;
  font-size: 11px;
  margin-left: 8px;
}
.editor-gallery {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.editor-gallery article {
  width: 190px;
  border: 1px solid #dce7df;
  background: white;
  padding: 10px;
  border-radius: 8px;
}
.editor-gallery img {
  width: 100%;
  height: 100px;
  object-fit: contain;
}
.product-editor label {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: #627b6c;
}
.product-editor input:not([type='file']),
.product-editor select,
.product-editor textarea {
  border: 1px solid #d5e3da;
  border-radius: 6px;
  padding: 8px;
  max-width: 100%;
  width: 100%;
  font: inherit;
  box-sizing: border-box;
}
.editor-block {
  margin: 14px 0;
  padding: 12px;
  border: 1px solid #dce7df;
  background: white;
  border-radius: 8px;
}
.editor-block textarea {
  width: 100%;
}
.editor-block img {
  max-height: 180px;
  max-width: 100%;
  object-fit: contain;
}
.editor-parameter {
  display: flex;
  gap: 10px;
  margin: 10px 0;
}
.editor-parameter input {
  min-width: 0;
  flex: 1;
}
.editor-form label {
  flex: 1;
  min-width: 140px;
}
.editor-notice {
  font-size: 13px;
  color: #168f86;
  white-space: pre-wrap;
}
</style>
