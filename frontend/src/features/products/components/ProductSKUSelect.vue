<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { fetchProductDetail } from '../api/productDetailsApi'
import type { ProductSKU } from '../model/types'

const props = defineProps<{
  productId: number | null
  modelValue?: number | null
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()
const choices = ref<ProductSKU[]>([])
const loading = ref(false)
const error = ref('')
let generation = 0
watch(
  () => props.productId,
  async (id) => {
    const current = ++generation
    const previous = props.modelValue
    choices.value = []
    error.value = ''
    loading.value = false
    emit('update:modelValue', null)
    if (!id) return
    loading.value = true
    try {
      const detail = await fetchProductDetail(id)
      if (current !== generation) return
      choices.value = detail.skus.filter((sku) => sku.active)
      const value =
        choices.value.find((sku) => sku.id === previous) ??
        (choices.value.length === 1 ? choices.value[0] : undefined)
      emit('update:modelValue', value?.id ?? null)
    } catch (reason) {
      if (current === generation)
        error.value = reason instanceof Error ? reason.message : 'SKU 读取失败'
    } finally {
      if (current === generation) loading.value = false
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  generation++
})
</script>
<template>
  <label class="sku-select-field"
    >颜色 / 尺寸<select
      :value="modelValue ?? ''"
      :disabled="disabled || loading || !productId"
      @change="
        emit('update:modelValue', Number(($event.target as HTMLSelectElement).value) || null)
      "
    >
      <option value="">{{ loading ? '正在读取规格…' : '选择具体 SKU' }}</option>
      <option v-for="sku in choices" :key="sku.id" :value="sku.id">
        {{ sku.label }} · {{ sku.code }} · 可售 {{ sku.stock }}
      </option></select
    ><small v-if="error" role="alert">{{ error }}，请重新选择商品重试。</small></label
  >
</template>
<style scoped>
.sku-select-field {
  display: grid;
  gap: 5px;
  font-size: 12px;
  color: #61766b;
}
.sku-select-field select {
  max-width: 100%;
  width: 100%;
  border: 1px solid #dce6df;
  padding: 9px;
  border-radius: 7px;
  background: white;
}
.sku-select-field small {
  color: #a45045;
}
</style>
