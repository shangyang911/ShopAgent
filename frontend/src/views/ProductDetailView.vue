<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowLeft, ChevronLeft, ChevronRight, Package, RefreshCw, X, ZoomIn } from '@lucide/vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import { fetchProductDetail } from '../features/products/api/productDetailsApi'
import { resolveProductImageUrl } from '../features/products/api/productsApi'
import type { ProductDetail } from '../features/products/model/types'
import ProductDetailEditor from '../features/products/components/ProductDetailEditor.vue'
import { cents, decimal, money } from '../shared/commerce'

defineProps<{ username: string }>()
const detail = ref<ProductDetail | null>(null)
const loading = ref(false)
const error = ref('')
const color = ref('')
const size = ref('')
const quantity = ref(1)
const currentImage = ref(0)
const zoomOpen = ref(false)
const lightbox = ref<HTMLDivElement | null>(null)
let previousFocus: HTMLElement | null = null
const editorOpen = ref(false)
const tab = ref<'description' | 'parameters' | 'inventory'>('description')
const backUrl = ref('/products')
let generation = 0
let disposed = false
const activeSKUs = computed(() => detail.value?.skus.filter((sku) => sku.active) ?? [])
const colorChoices = computed(() => [...new Set(activeSKUs.value.map((sku) => sku.color))])
const sizeChoices = computed(() => [...new Set(activeSKUs.value.map((sku) => sku.size))])
const selectedSKU = computed(() =>
  activeSKUs.value.find((sku) => sku.color === color.value && sku.size === size.value),
)
const images = computed(() => detail.value?.images ?? [])
const image = computed(() => images.value[currentImage.value] ?? images.value[0])
const available = computed(() => selectedSKU.value?.available_quantity ?? 0)
const selectionTotal = computed(() =>
  decimal(
    cents(selectedSKU.value?.price ?? detail.value?.price ?? 0) *
      BigInt(Number.isInteger(quantity.value) && quantity.value > 0 ? quantity.value : 0),
  ),
)
const orderUrl = computed(() =>
  selectedSKU.value && detail.value
    ? `/orders?create_product_id=${detail.value.id}&create_sku_id=${selectedSKU.value.id}&create_quantity=${quantity.value}`
    : '/orders',
)

function acceptDetail(value: ProductDetail) {
  const oldId = selectedSKU.value?.id
  detail.value = value
  const sku =
    value.skus.find((row) => row.id === oldId && row.active) ??
    value.skus.find((row) => row.active && row.stock > 0) ??
    value.skus.find((row) => row.active)
  color.value = sku?.color ?? ''
  size.value = sku?.size ?? ''
  currentImage.value = Math.min(currentImage.value, Math.max(0, value.images.length - 1))
}
async function load() {
  const params = new URLSearchParams(window.location.search)
  const returnTo = params.get('return')
  backUrl.value =
    returnTo &&
    ['/products', '/favorites'].some((path) => returnTo === path || returnTo.startsWith(`${path}?`))
      ? returnTo
      : '/products'
  const id = Number(params.get('id'))
  if (detail.value && detail.value.id !== id) detail.value = null
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('商品编号无效')
    const value = await fetchProductDetail(id)
    if (disposed || current !== generation) return
    acceptDetail(value)
  } catch (reason) {
    if (!disposed && current === generation)
      error.value = reason instanceof Error ? reason.message : '商品详情加载失败'
  } finally {
    if (!disposed && current === generation) loading.value = false
  }
}
function selectColor(value: string) {
  color.value = value
  if (!activeSKUs.value.some((sku) => sku.color === value && sku.size === size.value))
    size.value = activeSKUs.value.find((sku) => sku.color === value)?.size ?? ''
  const index = images.value.findIndex((row) => row.color === value)
  if (index >= 0) currentImage.value = index
}
function stepImage(delta: number) {
  if (images.value.length)
    currentImage.value = (currentImage.value + delta + images.value.length) % images.value.length
}
function keys(event: KeyboardEvent) {
  if (event.key === 'Escape') zoomOpen.value = false
  if (!zoomOpen.value) return
  if (event.key === 'Tab') {
    const buttons = Array.from(lightbox.value?.querySelectorAll<HTMLButtonElement>('button') ?? [])
    const first = buttons[0],
      last = buttons[buttons.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }
  if (event.key === 'ArrowLeft') stepImage(-1)
  if (event.key === 'ArrowRight') stepImage(1)
}
watch(zoomOpen, async (open) => {
  if (open) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    await nextTick()
    lightbox.value?.querySelector('button')?.focus()
  } else previousFocus?.focus()
})
watch(available, (value) => {
  quantity.value = Math.max(1, Math.min(quantity.value, value, 10000))
})
onMounted(() => {
  void load()
  window.addEventListener('popstate', load)
  window.addEventListener('keydown', keys)
})
onBeforeUnmount(() => {
  disposed = true
  generation++
  window.removeEventListener('popstate', load)
  window.removeEventListener('keydown', keys)
})
</script>

<template>
  <OperationsShell active-page="products" title="商品详情" :username="username">
    <header class="detail-page-heading">
      <p>PRODUCT DETAILS</p>
      <h1>商品详情</h1>
      <p>核对商品、规格和可售库存。</p>
    </header>
    <div class="detail-toolbar">
      <a :href="backUrl" class="ops-button"
        ><ArrowLeft :size="15" />{{
          backUrl.startsWith('/favorites') ? '返回收藏' : '返回商品列表'
        }}</a
      >
      <button class="ops-button" :disabled="loading" @click="load">
        <RefreshCw :size="15" />刷新库存
      </button>
      <button v-if="detail" class="ops-button primary" @click="editorOpen = !editorOpen">
        {{ editorOpen ? '收起管理' : '管理图片、详情与 SKU' }}
      </button>
    </div>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load">重试</button>
    </p>
    <p v-if="loading && !detail" class="module-empty">正在加载商品详情…</p>
    <template v-if="detail">
      <ProductDetailEditor v-if="editorOpen" :detail="detail" @changed="acceptDetail" />
      <section class="product-detail-hero" :aria-busy="loading">
        <div class="detail-gallery">
          <div class="detail-image-stage" aria-roledescription="轮播" aria-label="商品图片">
            <button
              v-if="image"
              class="detail-zoom"
              aria-label="放大商品图片"
              @click="zoomOpen = true"
            >
              <img
                :src="resolveProductImageUrl(image.url)"
                :alt="image.alt || detail.name"
              /><ZoomIn :size="20" />
            </button>
            <Package v-else :size="60" />
            <template v-if="images.length > 1">
              <button class="gallery-prev" aria-label="上一张图片" @click="stepImage(-1)">
                <ChevronLeft />
              </button>
              <button class="gallery-next" aria-label="下一张图片" @click="stepImage(1)">
                <ChevronRight />
              </button>
              <span class="gallery-count" aria-live="polite"
                >{{ currentImage + 1 }} / {{ images.length }}</span
              >
            </template>
          </div>
          <div class="detail-thumbnails" aria-label="选择商品图片">
            <button
              v-for="(photo, index) in images"
              :key="photo.id ?? photo.url"
              :class="{ active: currentImage === index }"
              :aria-label="`查看第 ${index + 1} 张商品图片`"
              :aria-pressed="currentImage === index"
              @click="currentImage = index"
            >
              <img :src="resolveProductImageUrl(photo.url)" :alt="photo.alt || detail.name" />
            </button>
          </div>
        </div>
        <div class="detail-buy-panel">
          <p class="detail-category">
            {{ detail.category }} · {{ detail.status === 'Active' ? '已上架' : '当前未上架' }}
          </p>
          <h2>{{ detail.name }}</h2>
          <p class="detail-price">
            {{ money(selectedSKU?.price ?? detail.price) }} <small>所选规格单价</small>
          </p>
          <fieldset v-if="colorChoices.length > 1 || colorChoices[0]" class="variant-options">
            <legend>
              颜色 <span>{{ color }}</span>
            </legend>
            <button
              v-for="value in colorChoices"
              :key="value"
              :class="{ active: color === value }"
              :aria-pressed="color === value"
              @click="selectColor(value)"
            >
              {{ value || '默认颜色' }}
            </button>
          </fieldset>
          <fieldset v-if="sizeChoices.length > 1 || sizeChoices[0]" class="variant-options">
            <legend>
              尺寸 <span>{{ size || '默认' }}</span>
            </legend>
            <button
              v-for="value in sizeChoices"
              :key="value"
              :class="{ active: size === value }"
              :aria-pressed="size === value"
              :disabled="!activeSKUs.some((sku) => sku.color === color && sku.size === value)"
              @click="size = value"
            >
              {{ value || '默认规格' }}
            </button>
          </fieldset>
          <p class="sku-label">
            SKU：{{ selectedSKU?.code || '请选择有效规格组合' }} · {{ selectedSKU?.label }}
          </p>
          <div class="detail-stock" role="status">
            <strong>{{ available > 0 ? `可售 ${available} 件` : '当前规格缺货' }}</strong
            ><span
              >已预留 {{ selectedSKU?.reserved_quantity ?? 0 }} 件 · 在库
              {{ selectedSKU?.on_hand_quantity ?? 0 }} 件</span
            >
          </div>
          <label class="detail-quantity"
            >数量<input
              v-model.number="quantity"
              type="number"
              min="1"
              :max="Math.min(available, 10000) || 1"
              step="1"
              :disabled="!available"
          /></label>
          <p>
            所选商品合计
            <strong>{{ money(selectionTotal) }}</strong>
          </p>
          <a
            v-if="
              selectedSKU &&
              available > 0 &&
              Number.isInteger(quantity) &&
              quantity > 0 &&
              quantity <= Math.min(available, 10000) &&
              ['Active', 'Low stock', 'Out of stock'].includes(detail.status)
            "
            :href="orderUrl"
            class="ops-button primary"
            >使用此规格创建订单 <ChevronRight :size="16"
          /></a>
          <button v-else class="ops-button primary" disabled>当前规格暂不可下单</button>
          <p class="ops-subtle">选择规格不会占用库存；提交订单时会再次核对并预留库存。</p>
        </div>
      </section>
      <section class="detail-tabs-panel">
        <div class="detail-tab-list" role="tablist" aria-label="商品详细信息">
          <button
            v-for="item in [
              ['description', '图文详情'],
              ['parameters', '规格参数'],
              ['inventory', '库存信息'],
            ] as const"
            :id="`tab-${item[0]}`"
            :key="item[0]"
            role="tab"
            :aria-selected="tab === item[0]"
            :aria-controls="`panel-${item[0]}`"
            :class="{ active: tab === item[0] }"
            @click="tab = item[0]"
          >
            {{ item[1] }}
          </button>
        </div>
        <div
          v-if="tab === 'description'"
          id="panel-description"
          role="tabpanel"
          aria-labelledby="tab-description"
          class="product-description"
        >
          <p v-if="!detail.description.length" class="ops-subtle">
            暂未添加图文详情，可在上方商品管理中补充。
          </p>
          <template v-for="(block, index) in detail.description" :key="index"
            ><p v-if="block.type === 'text'">{{ block.text }}</p>
            <img
              v-else-if="block.url"
              :src="resolveProductImageUrl(block.url)"
              :alt="`${detail.name} 详情图 ${index + 1}`"
              loading="lazy"
          /></template>
        </div>
        <dl
          v-else-if="tab === 'parameters'"
          id="panel-parameters"
          role="tabpanel"
          aria-labelledby="tab-parameters"
          class="detail-parameters"
        >
          <p v-if="!detail.parameters.length" class="ops-subtle">暂未填写规格参数。</p>
          <div v-for="parameter in detail.parameters" :key="parameter.name">
            <dt>{{ parameter.name }}</dt>
            <dd>{{ parameter.value }}</dd>
          </div>
        </dl>
        <div
          v-else
          id="panel-inventory"
          role="tabpanel"
          aria-labelledby="tab-inventory"
          class="sku-stock-list"
        >
          <p class="ops-subtle">
            商品可售总库存 {{ detail.stock }} 件。库存按具体 SKU 预留和出库。
          </p>
          <article v-for="sku in detail.skus" :key="sku.id">
            <strong
              >{{ sku.label }} <small>{{ sku.code }}</small></strong
            ><span>{{ money(sku.price) }}</span
            ><span
              >可售 {{ sku.stock }} · 预留 {{ sku.reserved }} · 在库
              {{ sku.on_hand_quantity }}</span
            ><span>{{ sku.active ? '启用' : '停用' }}</span>
          </article>
        </div>
      </section>
    </template>
    <div
      v-if="zoomOpen && image"
      ref="lightbox"
      class="product-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label="商品大图"
      @click.self="zoomOpen = false"
    >
      <button
        class="lightbox-close ops-button"
        aria-label="关闭大图"
        autofocus
        @click="zoomOpen = false"
      >
        <X /></button
      ><button class="ops-button" aria-label="上一张大图" @click="stepImage(-1)">
        <ChevronLeft /></button
      ><img :src="resolveProductImageUrl(image.url)" :alt="image.alt || detail?.name" /><button
        class="ops-button"
        aria-label="下一张大图"
        @click="stepImage(1)"
      >
        <ChevronRight />
      </button>
    </div>
  </OperationsShell>
</template>

<style scoped>
.detail-page-heading {
  margin: 4px 0 24px;
}
.detail-page-heading > p {
  color: #879990;
  font-size: 12px;
}
.detail-page-heading h1 {
  font-size: 28px;
  color: #263c32;
  margin: 10px 0;
}
.detail-toolbar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 22px;
}
.product-detail-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 0.85fr);
  gap: 40px;
  background: white;
  border: 1px solid #e1e9e5;
  border-radius: 16px;
  padding: 28px;
}
.detail-image-stage {
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 1.1;
  background: #f3f7f5;
  border-radius: 12px;
  overflow: hidden;
}
.detail-zoom {
  border: 0;
  background: transparent;
  width: 100%;
  height: 100%;
  cursor: zoom-in;
}
.detail-zoom img {
  width: 100%;
  height: 100%;
  max-height: 480px;
  object-fit: contain;
}
.detail-zoom svg {
  position: absolute;
  right: 15px;
  top: 15px;
  color: #64716e;
}
.gallery-prev,
.gallery-next {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  border: 0;
  background: #fff;
  border-radius: 50%;
  padding: 8px;
  cursor: pointer;
}
.gallery-prev {
  left: 12px;
}
.gallery-next {
  right: 12px;
}
.gallery-count {
  position: absolute;
  bottom: 12px;
  right: 15px;
  padding: 4px 9px;
  background: #fff;
  border-radius: 20px;
  font-size: 12px;
}
.detail-thumbnails {
  display: flex;
  gap: 9px;
  overflow: auto;
  padding-top: 12px;
}
.detail-thumbnails button {
  border: 2px solid transparent;
  border-radius: 8px;
  padding: 3px;
  background: #f4f7f5;
  flex-shrink: 0;
  cursor: pointer;
}
.detail-thumbnails button.active {
  border-color: #149e91;
}
.detail-thumbnails img {
  width: 64px;
  height: 64px;
  object-fit: contain;
}
.detail-category,
.sku-label {
  font-size: 12px;
  color: #7d8d87;
}
.detail-buy-panel h2 {
  font-size: 26px;
  line-height: 1.45;
  color: #263633;
  margin: 15px 0;
}
.detail-price {
  font-size: 30px;
  color: #168f86;
  font-weight: 700;
  margin: 25px 0;
}
.detail-price small {
  font-size: 12px;
  color: #87948e;
  font-weight: 400;
}
.variant-options {
  border: 0;
  padding: 0;
  margin: 22px 0;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.variant-options legend {
  margin-bottom: 9px;
  font-size: 13px;
  color: #667a70;
}
.variant-options legend span {
  margin-left: 16px;
  color: #263633;
}
.variant-options button {
  padding: 9px 18px;
  border: 1px solid #dce6e0;
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
}
.variant-options button.active {
  border-color: #149e91;
  background: #eef9f5;
  color: #13796c;
}
.detail-stock {
  display: grid;
  gap: 8px;
  padding: 16px;
  background: #f4f9f6;
  border-radius: 9px;
  font-size: 12px;
  color: #7b8c83;
}
.detail-stock strong {
  color: #237760;
  font-size: 15px;
}
.detail-quantity {
  display: flex;
  align-items: center;
  gap: 18px;
  margin: 20px 0;
  font-size: 13px;
}
.detail-quantity input {
  width: 85px;
  border: 1px solid #dae5de;
  border-radius: 7px;
  padding: 9px;
}
.detail-tabs-panel {
  border: 1px solid #e1e9e5;
  background: white;
  border-radius: 14px;
  margin-top: 24px;
  padding: 24px;
}
.detail-tab-list {
  display: flex;
  gap: 25px;
  border-bottom: 1px solid #e5ece7;
  margin-bottom: 24px;
}
.detail-tab-list button {
  background: transparent;
  border: 0;
  padding: 12px 2px;
  color: #7c8c84;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.detail-tab-list button.active {
  color: #168f86;
  border-color: #168f86;
}
.product-description {
  max-width: 900px;
  margin: auto;
  line-height: 1.9;
  color: #53675c;
}
.product-description p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.product-description img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 18px auto;
}
.detail-parameters div {
  display: grid;
  grid-template-columns: 160px 1fr;
  padding: 14px;
  background: #f6f9f7;
  margin-bottom: 3px;
  font-size: 13px;
}
.detail-parameters dt {
  color: #7c8c84;
}
.detail-parameters dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.sku-stock-list article {
  display: flex;
  justify-content: space-between;
  gap: 15px;
  padding: 15px 0;
  border-top: 1px solid #e5ece7;
  font-size: 12px;
  flex-wrap: wrap;
}
.sku-stock-list small {
  display: block;
  font-weight: 400;
  margin-top: 5px;
  color: #8a9991;
}
.product-lightbox {
  position: fixed;
  inset: 0;
  background: #12251ee8;
  z-index: 300;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 25px;
}
.product-lightbox > img {
  max-width: 80vw;
  max-height: 90vh;
  object-fit: contain;
}
.lightbox-close {
  position: absolute;
  right: 25px;
  top: 25px;
}
@media (max-width: 800px) {
  .product-detail-hero {
    grid-template-columns: minmax(0, 1fr);
    padding: 18px;
    gap: 20px;
  }
  .detail-buy-panel h2 {
    font-size: 22px;
  }
  .detail-tabs-panel {
    padding: 16px;
  }
  .detail-parameters div {
    grid-template-columns: 100px 1fr;
  }
  .product-lightbox {
    gap: 5px;
    padding: 8px;
  }
  .product-lightbox > img {
    max-width: 65vw;
  }
}
</style>
