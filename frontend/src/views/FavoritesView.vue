<script setup lang="ts">
import { Heart } from '@lucide/vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import ProductGrid from '../features/products/components/ProductGrid.vue'
import { useProductFavorites } from '../features/products/composables/useProductFavorites'
import type { Product } from '../features/products/model/types'
import { useI18n } from '../shared/i18n'

defineProps<{ username: string }>()
const { t } = useI18n()
const { favorites, favoriteIds, pendingIds, loading, ready, error, loadFavorites, toggleFavorite } =
  useProductFavorites()

function manageProduct(product: Product) {
  window.location.assign(
    `/products/detail?id=${product.id}&return=${encodeURIComponent('/favorites')}`,
  )
}
</script>

<template>
  <OperationsShell active-page="favorites" :title="t('Favorites')" :username="username">
    <section class="favorites-page">
      <div class="catalog-heading">
        <div>
          <div class="title-line">
            <h1>{{ t('Favorites') }}</h1>
          </div>
          <p>{{ t('Saved products') }} · {{ favorites.length }} {{ t('products') }}</p>
        </div>
        <a class="add-product-button" href="/products">{{ t('Browse products') }}</a>
      </div>
      <div v-if="!loading && !error && !favorites.length" class="empty-state">
        <Heart :size="32" />
        <strong>{{ t('No favorites yet') }}</strong>
        <span>{{ t('Tap the heart on a product to save it here.') }}</span>
        <a href="/products">{{ t('Browse products') }}</a>
      </div>
      <ProductGrid
        v-else
        :products="favorites"
        :loading="loading"
        :error="error"
        :favorite-ids="favoriteIds"
        :favorite-pending-ids="pendingIds"
        :favorites-ready="ready"
        @retry="loadFavorites"
        @favorite="toggleFavorite"
        @edit="manageProduct"
      />
    </section>
  </OperationsShell>
</template>
