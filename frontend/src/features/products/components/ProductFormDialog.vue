<script setup lang="ts">
// [商品管理页 / 新增与编辑弹窗] 表单只管理本地输入和图片预览，通过事件把保存/删除交回 ProductsView。
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Trash2, X } from '@lucide/vue'
import { useI18n } from '../../../shared/i18n'
import { resolveProductImageUrl } from '../api/productsApi'
import type { Product, ProductWrite } from '../model/types'

const props = defineProps<{
  open: boolean
  product: Product | null
  saving: boolean
  error: string
}>()

const emit = defineEmits<{
  close: []
  save: [product: ProductWrite, image: File | null]
  delete: [id: number]
}>()

const { t } = useI18n()
const selectedImage = ref<File | null>(null)
const imagePreview = ref('')
const imageError = ref('')
const form = reactive<ProductWrite>({
  name: '',
  category: '',
  price: 0,
  stock: 0,
  status: 'Active',
  color: '',
  rating: 0,
  reviews: 0,
  shipping: '',
})

// [商品表单 / 打开或切换商品] 新增时清空字段，编辑时把选中商品复制到可修改的表单状态。
watch(
  () => [props.open, props.product] as const,
  ([open, product]) => {
    if (!open) return
    if (imagePreview.value.startsWith('blob:')) URL.revokeObjectURL(imagePreview.value)
    selectedImage.value = null
    imageError.value = ''
    imagePreview.value = product ? resolveProductImageUrl(product.image_url) : ''
    Object.assign(
      form,
      product
        ? {
            name: product.name,
            category: product.category,
            price: product.price,
            stock: product.stock,
            status: ['Low stock', 'Out of stock'].includes(product.status)
              ? 'Active'
              : product.status,
            color: product.color,
            rating: product.rating,
            reviews: product.reviews,
            shipping: product.shipping,
          }
        : {
            name: '',
            category: '',
            price: 0,
            stock: 0,
            status: 'Active',
            color: '',
            rating: 0,
            reviews: 0,
            shipping: '',
          },
    )
  },
  { immediate: true },
)

// [商品表单 / 图片选择] 预先限制 5 MB，并建立可撤销的本地预览 URL。
function handleImageChange(event: Event) {
  const input = event.target as HTMLInputElement
  const image = input.files?.[0] ?? null
  if (image && image.size > 5 * 1024 * 1024) {
    imageError.value = t('Image must be 5 MB or smaller')
    input.value = ''
    return
  }
  imageError.value = ''
  selectedImage.value = image
  if (imagePreview.value.startsWith('blob:')) URL.revokeObjectURL(imagePreview.value)
  imagePreview.value = image
    ? URL.createObjectURL(image)
    : props.product
      ? resolveProductImageUrl(props.product.image_url)
      : ''
}

// [商品表单 / 组件卸载] 释放本地 blob URL，避免预览文件句柄长期占用内存。
onBeforeUnmount(() => {
  if (imagePreview.value.startsWith('blob:')) URL.revokeObjectURL(imagePreview.value)
})

// [商品表单 / 提交] 只回传规范化字段和可选图片，实际 API 请求由父页面处理。
function submit() {
  if (imageError.value) return
  emit('save', { ...form, name: form.name.trim() }, selectedImage.value)
}
</script>

<template>
  <!-- [商品表单弹窗] 点击遮罩关闭；内部表单将保存与删除操作通过事件交给父页面。 -->
  <div v-if="open" class="product-dialog-backdrop" @click.self="emit('close')">
    <section
      class="product-dialog"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="'product-dialog-title'"
    >
      <header class="product-dialog-header">
        <div>
          <h2 id="product-dialog-title">{{ product ? t('Edit product') : t('Add product') }}</h2>
          <p>{{ t('Product details') }}</p>
        </div>
        <button class="dialog-close" type="button" :aria-label="t('Cancel')" @click="emit('close')">
          <X :size="18" />
        </button>
      </header>

      <form class="product-form" @submit.prevent="submit">
        <div class="product-form-grid">
          <label class="form-field form-field-wide">
            <span>{{ t('Product name') }}</span>
            <input v-model="form.name" required maxlength="180" />
          </label>
          <label class="form-field">
            <span>{{ t('Category') }}</span>
            <input v-model="form.category" required maxlength="80" />
          </label>
          <label class="form-field">
            <!-- 经营状态由管理员设置；缺货和低库存由库存中心按阈值自动计算。 -->
            <span>{{ t('Status') }}</span>
            <select v-model="form.status" required>
              <option value="Active">{{ t('Active') }}</option>
              <option value="Inactive">下架</option>
              <option value="Archived">归档</option>
              <option value="Draft">{{ t('Draft') }}</option>
            </select>
          </label>
          <label class="form-field">
            <span>{{ t('Price') }}</span>
            <input v-model.number="form.price" type="number" min="0" step="0.01" required />
          </label>
          <label class="form-field">
            <span>{{ t('Stock') }}</span>
            <input
              v-model.number="form.stock"
              type="number"
              min="0"
              step="1"
              required
              :readonly="!!product"
            />
            <a v-if="product" href="/inventory" class="form-hint"
              >可售数量由库存流水维护，前往库存中心补货</a
            >
          </label>
          <label class="form-field">
            <span>{{ t('Color') }}</span>
            <input v-model="form.color" maxlength="40" />
          </label>
          <label class="form-field">
            <span>{{ t('Shipping Options') }}</span>
            <input v-model="form.shipping" maxlength="40" />
          </label>
          <div class="form-field form-field-wide">
            <span>{{ t('Product image') }}</span>
            <div class="image-picker-row">
              <img
                v-if="imagePreview"
                class="product-image-preview"
                :src="imagePreview"
                :alt="t('Image preview')"
              />
              <div v-else class="product-image-preview image-preview-empty">
                {{ t('No image selected') }}
              </div>
              <label class="image-file-picker">
                <span>{{ t('Choose image') }}</span>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  :required="!product"
                  @change="handleImageChange"
                />
              </label>
            </div>
            <small class="image-help">{{
              t('Supported images: JPEG, PNG, WebP. Max 5 MB.')
            }}</small>
            <small v-if="imageError" class="form-error" role="alert">{{ imageError }}</small>
          </div>
        </div>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>

        <footer class="product-dialog-footer">
          <button
            v-if="product"
            class="delete-product-button"
            type="button"
            :disabled="saving"
            @click="emit('delete', product.id)"
          >
            <Trash2 :size="15" /> {{ t('Delete product') }}
          </button>
          <span v-else></span>
          <div>
            <button
              class="secondary-action"
              type="button"
              :disabled="saving"
              @click="emit('close')"
            >
              {{ t('Cancel') }}
            </button>
            <button class="primary-action" type="submit" :disabled="saving">
              {{ saving ? t('Saving…') : t('Save product') }}
            </button>
          </div>
        </footer>
      </form>
    </section>
  </div>
</template>
