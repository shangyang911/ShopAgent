import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { addFavorite, fetchFavorites, removeFavorite } from '../api/favoritesApi'
import type { Product } from '../model/types'
import { useI18n } from '../../../shared/i18n'

export function useProductFavorites() {
  const { t } = useI18n()
  const favorites = ref<Product[]>([])
  const loading = ref(true)
  const ready = ref(false)
  const error = ref('')
  const pendingIds = ref<number[]>([])
  const favoriteIds = computed(() => favorites.value.map((product) => product.id))
  let generation = 0
  let disposed = false

  async function loadFavorites() {
    if (disposed || pendingIds.value.length) return
    const id = ++generation
    loading.value = true
    ready.value = false
    error.value = ''
    try {
      const result = await fetchFavorites()
      if (disposed || id !== generation) return
      favorites.value = result
      ready.value = true
    } catch (reason) {
      if (!disposed && id === generation)
        error.value = reason instanceof Error ? reason.message : t('Favorites loading failed')
    } finally {
      if (!disposed && id === generation) loading.value = false
    }
  }

  async function toggleFavorite(product: Product) {
    if (disposed || !ready.value || loading.value || pendingIds.value.includes(product.id)) return
    pendingIds.value.push(product.id)
    error.value = ''
    try {
      if (favoriteIds.value.includes(product.id)) {
        await removeFavorite(product.id)
        if (disposed) return
        favorites.value = favorites.value.filter((item) => item.id !== product.id)
      } else {
        await addFavorite(product.id)
        if (disposed) return
        favorites.value = [product, ...favorites.value]
      }
    } catch (reason) {
      if (!disposed)
        error.value = reason instanceof Error ? reason.message : t('Favorite update failed')
    } finally {
      pendingIds.value = pendingIds.value.filter((id) => id !== product.id)
    }
  }

  onMounted(loadFavorites)
  onBeforeUnmount(() => {
    disposed = true
    generation++
  })
  return {
    favorites,
    favoriteIds,
    pendingIds,
    loading,
    ready,
    error,
    loadFavorites,
    toggleFavorite,
  }
}
