<script setup lang="ts">
// 复用控制器处理事务交互，模板仅展示预览，不自行拼接请求。
import { toRef, watch } from 'vue'
import type { Product } from '../model/types'
import { useProductBatch } from '../composables/useProductBatch'
import { useI18n } from '../../../shared/i18n'
const props = defineProps<{ products: Product[]; disabled: boolean }>()
const selectedIds = defineModel<number[]>('selectedIds', { required: true })
const busyModel = defineModel<boolean>('busy', { default: false })
const emit = defineEmits<{ applied: [] }>()
const { t } = useI18n()
const { action, preview, busy, error, success, allSelected, selectAll, prepare, apply } =
  useProductBatch(toRef(props, 'products'), selectedIds, () => emit('applied'))
watch(busy, (value) => {
  busyModel.value = value
})
const labels: Record<string, string> = { 已收藏: '已收藏', 未收藏: '未收藏' }
const label = (value: string) => labels[value] ?? t(value)
</script>

<template>
  <section class="product-batch-panel" aria-label="商品批量操作">
    <div class="batch-controls">
      <label
        ><input
          type="checkbox"
          :checked="allSelected"
          :disabled="busy || disabled || !products.length"
          @change="selectAll"
        />
        全选当前页</label
      >
      <span>已选 {{ selectedIds.length }} 件</span>
      <select v-model="action" aria-label="批量操作类型" :disabled="busy">
        <option value="activate">批量上架</option>
        <option value="deactivate">批量下架</option>
        <option value="archive">批量归档</option>
        <option value="favorite">批量收藏</option>
        <option value="unfavorite">取消收藏</option>
      </select>
      <button
        class="ops-button"
        :disabled="busy || disabled || !selectedIds.length"
        @click="prepare"
      >
        {{ busy ? '处理中…' : '预览变更' }}
      </button>
    </div>
    <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
    <p v-if="success" role="status">{{ success }}</p>
    <div v-if="preview" class="batch-preview">
      <p>请核对以下 {{ preview.items.length }} 件商品，确认后统一提交。</p>
      <ul>
        <li v-for="item in preview.items" :key="item.id">
          <strong>{{ item.name }}</strong
          ><span>{{ label(item.before) }} → {{ label(item.after) }}</span>
        </li>
      </ul>
      <div class="section-actions">
        <button class="ops-button" :disabled="busy" @click="preview = null">取消</button
        ><button class="ops-button primary" :disabled="busy || disabled" @click="apply">
          确认执行
        </button>
      </div>
    </div>
  </section>
</template>
