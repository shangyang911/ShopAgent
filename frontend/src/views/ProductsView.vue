<script setup lang="ts">
// [商品列表页] 组合页面布局和商品功能状态；请求与筛选业务由 useProductsCatalog 管理。
import { inject, ref } from 'vue'
import { notificationNavigationKey } from '../shared/navigation/notifications'
import {
  Bell,
  ChevronDown,
  ChevronRight,
  LogOut,
  Mail,
  Menu,
  Package,
  Plus,
  Search,
} from '@lucide/vue'
import LanguageSwitcher from '../shared/components/LanguageSwitcher.vue'
import { useI18n } from '../shared/i18n'
import ProductFormDialog from '../features/products/components/ProductFormDialog.vue'
import ProductFiltersPanel from '../features/products/components/ProductFiltersPanel.vue'
import ProductGrid from '../features/products/components/ProductGrid.vue'
import ProductBatchPanel from '../features/products/components/ProductBatchPanel.vue'
import AppPagination from '../shared/components/AppPagination.vue'
import AppSidebar from '../shared/components/AppSidebar.vue'
import {
  createProduct,
  deleteProduct,
  fetchProductOptions,
  updateProduct,
} from '../features/products/api/productsApi'
import type { Product, ProductWrite } from '../features/products/model/types'
import { useProductsCatalog } from '../features/products/composables/useProductsCatalog'
import { logout } from '../features/auth/api/authApi'
import { useProductFavorites } from '../features/products/composables/useProductFavorites'

// [商品列表页 / 账号信息] 用户名由 App.vue 在确认登录态后传入。
defineProps<{ username: string }>()

const { t } = useI18n()
const notifications = inject(notificationNavigationKey, undefined)
const dialogOpen = ref(false)
const editingProduct = ref<Product | null>(null)
const savingProduct = ref(false)
const mutationError = ref('')
const selectedProductIds = ref<number[]>([])
const batchBusy = ref(false)
function toggleProductSelection(id: number) {
  if (batchBusy.value) return
  selectedProductIds.value = selectedProductIds.value.includes(id)
    ? selectedProductIds.value.filter((value) => value !== id)
    : [...selectedProductIds.value, id]
}
async function refreshAfterBatch() {
  await Promise.all([refreshCatalog(), loadFavorites()])
}
const {
  favoriteIds,
  pendingIds: favoritePendingIds,
  ready: favoritesReady,
  error: favoritesError,
  loadFavorites,
  toggleFavorite,
} = useProductFavorites()
const {
  products,
  options,
  total,
  loading,
  error,
  mobileFiltersOpen,
  searchDraft,
  filters,
  pageCount,
  resultLabel,
  priceRanges,
  activeFilterCount,
  loadProducts,
  toggleValue,
  setPrice,
  applySearch,
  clearFilters,
  changePage,
  setSort,
  setStatus,
} = useProductsCatalog()

function openCreateDialog() {
  // [商品列表页 / 新建商品] 清空编辑上下文后打开空白表单。
  editingProduct.value = null
  mutationError.value = ''
  dialogOpen.value = true
}

function openEditDialog(product: Product) {
  // [商品列表页 / 编辑商品] 保存当前卡片数据，交由弹窗回填表单。
  editingProduct.value = product
  mutationError.value = ''
  dialogOpen.value = true
}

function closeDialog() {
  // [商品列表页 / 关闭表单] 保存期间不允许丢弃正在提交的编辑状态。
  if (savingProduct.value) return
  dialogOpen.value = false
  mutationError.value = ''
}

async function refreshCatalog() {
  // [商品列表页 / 刷新目录] 先更新筛选项，再刷新当前页商品结果。
  try {
    options.value = await fetchProductOptions()
  } catch {
    // The product list can still refresh if filter options are temporarily unavailable.
  }
  await loadProducts()
}

async function saveProduct(product: ProductWrite, image: File | null) {
  // [商品列表页 / 保存商品] 根据当前编辑对象选择新增或更新，并向用户显示 API 错误。
  savingProduct.value = true
  mutationError.value = ''
  try {
    if (editingProduct.value)
      await updateProduct(editingProduct.value.id, product, image ?? undefined)
    else {
      if (!image) throw new Error(t('Product image required'))
      await createProduct(product, image)
    }
    dialogOpen.value = false
    await refreshCatalog()
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : t('Product save failed')
  } finally {
    savingProduct.value = false
  }
}

async function removeProduct(productId: number) {
  // [商品列表页 / 删除商品] 用户确认后删除记录；删除当前页末条时退回上一页。
  if (!window.confirm(t('Delete product confirmation'))) return
  savingProduct.value = true
  mutationError.value = ''
  try {
    await deleteProduct(productId)
    if (total.value - 1 <= (filters.page - 1) * filters.page_size && filters.page > 1) {
      filters.page -= 1
    }
    dialogOpen.value = false
    await refreshCatalog()
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : t('Product delete failed')
  } finally {
    savingProduct.value = false
  }
}

function toggleFilterValue(field: 'category' | 'color' | 'shipping', value: string) {
  // [商品列表页 / 多选筛选] 将面板事件转发给目录状态 composable。
  toggleValue(filters[field], value)
}

async function signOut() {
  // [后台导航 / 退出登录] 撤销后端 Cookie 会话并返回登录页。
  try {
    await logout()
    window.location.assign('/login')
  } catch (error) {
    mutationError.value = error instanceof Error ? error.message : t('Sign out failed')
  }
}
</script>

<template>
  <!-- [商品管理页] 页面视图负责组合共享导航、商品筛选、结果列表和分页组件。 -->
  <main class="app-frame">
    <AppSidebar />

    <section class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          <span>{{ t('Corelystics Store') }}</span> <ChevronRight :size="15" /><strong>
            <Package :size="15" /> {{ t('Products') }}
          </strong>
        </div>
        <form class="search-box" @submit.prevent="applySearch">
          <Search :size="18" />
          <input
            v-model="searchDraft"
            :placeholder="t('Search products...')"
            :aria-label="t('Search products...')"
          />
          <kbd>⌘ + F</kbd>
          <button type="submit" :aria-label="t('Submit search')">
            <Search :size="16" />
          </button>
        </form>
        <div class="top-actions">
          <LanguageSwitcher />
          <span class="signed-in-user" :title="username">{{ username }}</span>
          <button
            class="icon-button"
            :aria-label="t('Sign out')"
            :title="t('Sign out')"
            @click="signOut"
          >
            <LogOut :size="18" />
          </button>
          <a href="/notifications" class="icon-button" :aria-label="t('Notifications')">
            <Bell :size="18" /><i v-if="notifications && notifications.unreadCount.value > 0"></i>
          </a>
          <button class="icon-button" :aria-label="t('Messages')">
            <Mail :size="18" />
          </button>
          <button class="icon-button bag-button" :aria-label="t('Orders')">
            <Package :size="18" />
          </button>
        </div>
      </header>

      <div class="mobile-toolbar">
        <button @click="mobileFiltersOpen = !mobileFiltersOpen">
          <Menu :size="18" /> {{ t('Filters') }}
          <span v-if="activeFilterCount">{{ activeFilterCount }}</span>
        </button>
        <span>{{ total }} {{ t('products') }}</span>
      </div>

      <div class="catalog-layout">
        <ProductFiltersPanel
          :filters="filters"
          :options="options"
          :active-filter-count="activeFilterCount"
          :mobile-open="mobileFiltersOpen"
          :price-ranges="priceRanges"
          @toggle-value="toggleFilterValue"
          @set-price="setPrice"
          @set-status="setStatus"
          @clear="clearFilters"
          @close="mobileFiltersOpen = false"
        />

        <section class="products-area">
          <div class="catalog-heading">
            <div>
              <div class="title-line">
                <h1>{{ t('Products') }}</h1>
                <button class="more-button" :title="t('More options')">•••</button>
                <button class="add-product-button" @click="openCreateDialog">
                  <Plus :size="15" /> {{ t('Add product') }}
                </button>
              </div>
              <p>
                {{ t('Showing') }} <strong>{{ total.toLocaleString() }}</strong>
                {{ t('products in') }} <a>{{ resultLabel }}</a>
              </p>
            </div>
            <label class="sort-control"
              ><span>{{ t('Sort by:') }}</span
              ><select :value="filters.sort" @change="setSort">
                <option value="featured">{{ t('Featured') }}</option>
                <option value="price_asc">{{ t('Price: low to high') }}</option>
                <option value="price_desc">{{ t('Price: high to low') }}</option>
                <option value="stock">{{ t('Stock: low to high') }}</option>
              </select>
              <ChevronDown :size="14" />
            </label>
          </div>

          <ProductBatchPanel
            v-model:selected-ids="selectedProductIds"
            v-model:busy="batchBusy"
            :products="products"
            :disabled="loading || savingProduct || !favoritesReady"
            @applied="refreshAfterBatch"
          />
          <div v-if="favoritesError" class="notice error-notice" role="alert">
            <span>{{ favoritesError }}</span>
            <button @click="loadFavorites">{{ t('Retry') }}</button>
          </div>
          <ProductGrid
            :products="products"
            selectable
            :selected-ids="selectedProductIds"
            :selection-disabled="batchBusy"
            @toggle-selection="toggleProductSelection"
            :favorite-ids="favoriteIds"
            :favorite-pending-ids="favoritePendingIds"
            :favorites-ready="favoritesReady"
            :loading="loading"
            :error="error"
            @retry="loadProducts"
            @clear-filters="clearFilters"
            @edit="openEditDialog"
            @favorite="toggleFavorite"
          />
          <AppPagination
            :page="filters.page"
            :page-size="filters.page_size"
            :page-count="pageCount"
            :total="total"
            :loading="loading"
            @change="changePage"
          />
        </section>
      </div>
    </section>

    <ProductFormDialog
      :open="dialogOpen"
      :product="editingProduct"
      :saving="savingProduct"
      :error="mutationError"
      @close="closeDialog"
      @save="saveProduct"
      @delete="removeProduct"
    />
  </main>
</template>
