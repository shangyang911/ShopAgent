<script setup lang="ts">
// [商品列表页 / 筛选面板] 展示目录筛选控件，筛选状态由页面 composable 持有。
import { ChevronDown, SlidersHorizontal, X } from '@lucide/vue'
import { useI18n } from '../../../shared/i18n'
import type { ProductFilters, ProductOptions } from '../model/types'

defineProps<{
  filters: ProductFilters
  options: ProductOptions
  activeFilterCount: number
  mobileOpen: boolean
  priceRanges: { label: string; min: number; max?: number }[]
}>()

const emit = defineEmits<{
  toggleValue: [field: 'category' | 'color' | 'shipping', value: string]
  setPrice: [min?: number, max?: number]
  setStatus: [value: string]
  clear: []
  close: []
}>()

const { t, translateValue } = useI18n()
</script>

<template>
  <!-- [筛选面板 / 已选条件] 每个标签可单独移除；筛选值由父页面维护。 -->
  <aside class="filter-card" :class="{ 'mobile-open': mobileOpen }">
    <div class="filter-title">
      <div>
        <SlidersHorizontal :size="16" /><strong>{{ t('Filters') }}</strong>
        <span v-if="activeFilterCount" class="filter-count">{{ activeFilterCount }}</span>
      </div>
      <button class="clear-button" @click="emit('clear')">{{ t('Clear All') }}</button>
      <button class="close-filters" :aria-label="t('Close filters')" @click="emit('close')">
        <X :size="18" />
      </button>
    </div>

    <div v-if="activeFilterCount" class="active-chips">
      <button
        v-for="category in filters.category"
        :key="category"
        @click="emit('toggleValue', 'category', category)"
      >
        {{ t('Category:') }} {{ translateValue(category) }} <X :size="12" />
      </button>
      <button
        v-for="color in filters.color"
        :key="color"
        @click="emit('toggleValue', 'color', color)"
      >
        {{ t('Color:') }} {{ translateValue(color) }} <X :size="12" />
      </button>
      <button
        v-if="filters.min_price !== undefined || filters.max_price !== undefined"
        @click="emit('setPrice')"
      >
        {{ t('Price range') }} <X :size="12" />
      </button>
    </div>

    <!-- [筛选面板 / 分类、价格、颜色、评分、配送方式和状态] 控件只发出事件，不直接请求 API。 -->
    <section class="filter-section">
      <h3>{{ t('Category') }} <ChevronDown :size="15" /></h3>
      <label v-for="category in options.categories" :key="category" class="check-row">
        <input
          type="checkbox"
          :checked="filters.category.includes(category)"
          @change="emit('toggleValue', 'category', category)"
        />
        <span>{{ translateValue(category) }}</span>
      </label>
    </section>

    <section class="filter-section">
      <h3>{{ t('Price') }} <ChevronDown :size="15" /></h3>
      <button
        v-for="range in priceRanges"
        :key="range.label"
        class="check-row price-range"
        :class="{ checked: filters.min_price === range.min && filters.max_price === range.max }"
        @click="emit('setPrice', range.min, range.max)"
      >
        <span class="fake-checkbox">✓</span><span>{{ range.label }}</span>
      </button>
      <div class="price-inputs">
        <label>
          <span class="sr-only">{{ t('Minimum price') }}</span>
          <input
            type="number"
            min="0"
            placeholder="0"
            :value="filters.min_price ?? ''"
            @change="
              emit(
                'setPrice',
                Number(($event.target as HTMLInputElement).value) || undefined,
                filters.max_price,
              )
            "
          />
          <small>{{ t('min') }}</small>
        </label>
        <i>–</i>
        <label>
          <span class="sr-only">{{ t('Maximum price') }}</span>
          <input
            type="number"
            min="0"
            placeholder="0"
            :value="filters.max_price ?? ''"
            @change="
              emit(
                'setPrice',
                filters.min_price,
                Number(($event.target as HTMLInputElement).value) || undefined,
              )
            "
          />
          <small>{{ t('max') }}</small>
        </label>
      </div>
    </section>

    <section class="filter-section">
      <h3>{{ t('Color') }} <ChevronDown :size="15" /></h3>
      <div class="color-options">
        <button
          v-for="color in options.colors"
          :key="color"
          class="color-chip"
          :class="[`color-${color.toLowerCase()}`, { chosen: filters.color.includes(color) }]"
          @click="emit('toggleValue', 'color', color)"
        >
          <i></i>{{ translateValue(color) }}
        </button>
      </div>
    </section>

    <section class="filter-section shipping-section">
      <h3>{{ t('Shipping Options') }} <ChevronDown :size="15" /></h3>
      <label v-for="shipping in options.shipping_options" :key="shipping" class="check-row">
        <input
          type="checkbox"
          :checked="filters.shipping.includes(shipping)"
          @change="emit('toggleValue', 'shipping', shipping)"
        />
        <span>{{ translateValue(shipping) }}</span>
      </label>
    </section>

    <section class="filter-section">
      <h3>{{ t('Status') }} <ChevronDown :size="15" /></h3>
      <label
        v-for="status in ['Active', 'Inactive', 'Draft', 'Archived']"
        :key="status"
        class="check-row"
      >
        <input
          type="radio"
          name="status"
          :checked="filters.status === status"
          @change="emit('setStatus', status)"
        />
        <span>{{ translateValue(status) }}</span>
      </label>
    </section>
  </aside>
</template>
