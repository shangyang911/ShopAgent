// [商品功能 / 列表状态] 封装商品查询、筛选、排序和分页状态，供商品列表页复用。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { fetchProductOptions, fetchProducts } from '../api/productsApi'
import type { Product, ProductFilters, ProductOptions } from '../model/types'
import { useI18n } from '../../../shared/i18n'

export function useProductsCatalog() {
  const { t, translateValue } = useI18n()
  const products = ref<Product[]>([])
  const options = ref<ProductOptions>({ categories: [], colors: [], shipping_options: [] })
  const total = ref(0)
  const loading = ref(true)
  const error = ref('')
  const mobileFiltersOpen = ref(false)
  const searchDraft = ref('')
  let generation = 0
  let disposed = false
  const filters = reactive<ProductFilters>({
    q: '',
    category: [],
    color: [],
    shipping: [],
    min_price: undefined,
    max_price: undefined,
    status: undefined,
    sort: 'featured',
    page: 1,
    page_size: 12,
  })

  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / filters.page_size)))
  const resultLabel = computed(() =>
    filters.category.length ? filters.category.map(translateValue).join(', ') : t('all categories'),
  )
  const priceRanges = computed(() => [
    { label: '¥0–¥50', min: 0, max: 50 },
    { label: '¥50–¥75', min: 50, max: 75 },
    { label: '¥75–¥150', min: 75, max: 150 },
    { label: `${t('Over')} ¥150`, min: 150, max: undefined },
  ])
  const activeFilterCount = computed(
    () =>
      filters.category.length +
      filters.color.length +
      filters.shipping.length +
      Number(filters.min_price !== undefined || filters.max_price !== undefined) +
      Number(Boolean(filters.status)),
  )

  async function loadProducts() {
    // 切换分类、状态或页码时，旧请求不能覆盖最新筛选，也不能提前结束加载状态。
    const id = ++generation
    loading.value = true
    error.value = ''
    try {
      const result = await fetchProducts(filters)
      if (disposed || id !== generation) return
      total.value = result.total
      if (filters.page > pageCount.value) {
        // 删除末页最后一条或筛选后总数缩小时，回到有效页重新获取。
        filters.page = pageCount.value
        await loadProducts()
        return
      }
      products.value = result.items
    } catch (err) {
      if (!disposed && id === generation)
        error.value = err instanceof Error ? err.message : t('Product loading failed')
    } finally {
      if (!disposed && id === generation) loading.value = false
    }
  }

  function toggleValue(values: string[], value: string) {
    // [商品列表 / 多选筛选] 切换选中值后回到第一页并重新查询。
    const index = values.indexOf(value)
    if (index === -1) values.push(value)
    else values.splice(index, 1)
    filters.page = 1
    loadProducts()
  }

  function setPrice(min?: number, max?: number) {
    // [商品列表 / 价格筛选] 同步价格上下限，清空某一端时用 undefined 表示无边界。
    filters.min_price = min
    filters.max_price = max
    filters.page = 1
    loadProducts()
  }

  function applySearch() {
    // [商品列表 / 搜索框] 只在用户提交时应用草稿搜索词，避免每次输入都发请求。
    filters.q = searchDraft.value.trim()
    filters.page = 1
    loadProducts()
  }

  function clearFilters() {
    // [商品列表 / 清除筛选] 重置所有过滤条件和搜索草稿，再从第一页加载目录。
    filters.q = ''
    searchDraft.value = ''
    filters.category = []
    filters.color = []
    filters.shipping = []
    filters.min_price = undefined
    filters.max_price = undefined
    filters.status = undefined
    filters.page = 1
    loadProducts()
  }

  function changePage(page: number) {
    // [商品列表 / 分页] 将目标页限制在当前页数范围内后请求数据。
    filters.page = Math.min(Math.max(page, 1), pageCount.value)
    loadProducts()
  }

  function setSort(event: Event) {
    // [商品列表 / 排序] 从排序下拉框读取后端支持的排序键并回到第一页。
    filters.sort = (event.target as HTMLSelectElement).value
    filters.page = 1
    loadProducts()
  }

  function setStatus(status: string) {
    // 经营状态筛选回到第一页；库存风险由库存中心的阈值计算。
    filters.status = status
    filters.page = 1
    loadProducts()
  }

  onMounted(async () => {
    try {
      const result = await fetchProductOptions()
      if (disposed) return
      options.value = result
    } catch (err) {
      if (disposed) return
      error.value = err instanceof Error ? err.message : t('Filter options failed to load')
    }
    await loadProducts()
  })
  onBeforeUnmount(() => {
    disposed = true
    generation++
  })

  return {
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
  }
}
