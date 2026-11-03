<script setup lang="ts">
// [商品列表页 / 商品网格] 展示加载、错误、空结果状态及商品卡片。
import { Heart, Search, SlidersHorizontal, Tag, Truck } from '@lucide/vue'
import { useI18n } from '../../../shared/i18n'
import { resolveProductImageUrl } from '../api/productsApi'
import type { Product } from '../model/types'

defineProps<{
  products: Product[]
  loading: boolean
  error: string
  favoriteIds: number[]
  favoritePendingIds: number[]
  favoritesReady: boolean
  selectable?: boolean
  selectedIds?: number[]
  selectionDisabled?: boolean
}>()

const emit = defineEmits<{
  retry: []
  clearFilters: []
  edit: [product: Product]
  favorite: [product: Product]
  toggleSelection: [id: number]
}>()

const { t, translateValue } = useI18n()
const detailUrl = (id: number) =>
  `/products/detail?id=${id}&return=${encodeURIComponent(window.location.pathname + window.location.search)}`
// 标签来自后端统一库存口径，经营状态不会被缺货风险覆盖。
const inventoryLabels: Record<string, string> = {
  low_stock: '低库存',
  out_of_stock: '缺货',
  normal: '库存正常',
  unmonitored: '未监控',
}

function statusClass(status: string) {
  // 商品标签仅展示经营状态；旁边的库存风险来自后端统一阈值，禁止写死 10 件。
  if (status === 'Active') return 'active'
  if (status === 'Low stock') return 'low-stock'
  if (status === 'Out of stock') return 'out-of-stock'
  return 'draft'
}
</script>

<template>
  <div v-if="error" class="notice error-notice">
    <span>{{ error }}</span
    ><button @click="emit('retry')">{{ t('Retry') }}</button>
  </div>
  <div v-else-if="loading" class="empty-state">{{ t('Loading products…') }}</div>
  <div v-else-if="products.length" class="product-grid">
    <article v-for="product in products" :key="product.id" class="product-card">
      <div class="product-image">
        <a :href="detailUrl(product.id)" :aria-label="`查看 ${translateValue(product.name)} 详情`"
          ><img
            :src="resolveProductImageUrl(product.image_url)"
            :alt="translateValue(product.name)"
            loading="lazy"
        /></a>
        <span class="status-pill" :class="statusClass(product.status)">{{
          translateValue(product.status)
        }}</span>
        <button
          class="favorite"
          :class="{ 'is-favorite': favoriteIds.includes(product.id) }"
          :aria-pressed="favoriteIds.includes(product.id)"
          :aria-label="`${t(favoriteIds.includes(product.id) ? 'Remove favorite' : 'Favorite')} ${translateValue(product.name)}`"
          :title="t(favoriteIds.includes(product.id) ? 'Remove favorite' : 'Favorite')"
          :disabled="!favoritesReady || favoritePendingIds.includes(product.id)"
          @click="emit('favorite', product)"
        >
          <Heart :size="17" />
        </button>
      </div>
      <div class="product-info">
        <label v-if="selectable" class="product-select"
          ><input
            type="checkbox"
            :checked="selectedIds?.includes(product.id)"
            :disabled="selectionDisabled"
            :aria-label="`选择 ${translateValue(product.name)}`"
            @change="emit('toggleSelection', product.id)"
          />
          选择商品</label
        >
        <div class="product-name-row">
          <h2 :title="translateValue(product.name)">
            <a :href="detailUrl(product.id)" style="color: inherit; text-decoration: none">{{
              translateValue(product.name)
            }}</a>
          </h2>
          <span
            class="stock-indicator"
            :class="{
              'is-low': product.inventory_status === 'low_stock',
              'is-empty': product.stock === 0,
            }"
          >
            {{ product.stock }} {{ t('in stock') }} ·
            {{ inventoryLabels[product.inventory_status || 'unmonitored'] }}
          </span>
        </div>
        <div class="product-meta">
          <span><Tag :size="12" />{{ translateValue(product.category) }}</span>
          <span v-if="product.shipping" class="shipping-tag"
            ><Truck :size="12" />{{ translateValue(product.shipping) }}</span
          >
          <span
            v-if="product.color"
            class="color-dot"
            :class="`color-${product.color.toLowerCase()}`"
          ></span>
          {{ translateValue(product.color) }}
        </div>
        <div class="card-footer">
          <strong class="price"><small>¥</small>{{ product.price.toFixed(2) }}</strong>
          <button class="edit-button" :title="t('Edit product')" @click="emit('edit', product)">
            <SlidersHorizontal :size="14" /> {{ t('Manage') }}
          </button>
        </div>
      </div>
    </article>
  </div>
  <div v-else class="empty-state">
    <Search :size="24" /><strong>{{ t('No products found') }}</strong>
    <span>{{ t('Try adjusting your filters or search.') }}</span>
    <button @click="emit('clearFilters')">{{ t('Clear filters') }}</button>
  </div>
</template>
