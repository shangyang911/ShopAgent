import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { applyProductBatch, previewProductBatch } from '../api/productBatchApi'
import type { ProductBatchAction, ProductBatchPreview } from '../model/batch'
import type { Product } from '../model/types'

// 控制器管理选择、预览和提交；组件只显示状态。选择仅限当前结果页。
export function useProductBatch(
  products: Readonly<Ref<Product[]>>,
  selectedIds: Ref<number[]>,
  onApplied: () => void,
) {
  const action = ref<ProductBatchAction>('activate')
  const preview = ref<ProductBatchPreview | null>(null)
  const busy = ref(false)
  const error = ref('')
  const success = ref('')
  const allSelected = computed(
    () =>
      products.value.length > 0 && products.value.every((p) => selectedIds.value.includes(p.id)),
  )
  let generation = 0
  let submissionKey = ''
  watch(products, () => {
    selectedIds.value = []
    preview.value = null
    generation++
  })
  watch(
    [selectedIds, action],
    () => {
      preview.value = null
      error.value = ''
      generation++
    },
    { deep: true, flush: 'sync' },
  )

  function selectAll() {
    if (busy.value) return
    selectedIds.value = allSelected.value ? [] : products.value.map((product) => product.id)
  }
  async function prepare() {
    if (busy.value || !selectedIds.value.length) return
    const id = ++generation
    busy.value = true
    error.value = ''
    success.value = ''
    try {
      const result = await previewProductBatch([...selectedIds.value], action.value)
      if (id !== generation) return
      preview.value = result
      // 传输失败后沿用同一提交键，明确重新预览才创建新键。
      submissionKey = crypto.randomUUID()
    } catch (reason) {
      if (id === generation) error.value = reason instanceof Error ? reason.message : '批量预览失败'
    } finally {
      busy.value = false
    }
  }
  async function apply() {
    if (busy.value || !preview.value) return
    busy.value = true
    error.value = ''
    try {
      const result = await applyProductBatch(
        [...selectedIds.value],
        action.value,
        preview.value.snapshot,
        submissionKey,
      )
      preview.value = null
      selectedIds.value = []
      success.value = `已完成 ${result.count} 件商品的批量操作`
      onApplied()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '批量操作失败'
    } finally {
      busy.value = false
    }
  }
  onBeforeUnmount(() => {
    generation++
  })
  return { action, preview, busy, error, success, allSelected, selectAll, prepare, apply }
}
