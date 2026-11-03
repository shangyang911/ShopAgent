// [前端 API / 商品列表页] 集中封装商品列表和筛选项请求，避免视图组件直接拼接后端 URL。
import type {
  Product,
  ProductFilters,
  ProductListResponse,
  ProductOptions,
  ProductWrite,
} from '../model/types'
import { useI18n } from '../../../shared/i18n'
import { API_BASE } from '../../../shared/api/config'
import { request } from '../../../shared/api/httpClient'

export function resolveProductImageUrl(imageUrl: string): string {
  // [商品图片 / URL 解析] 绝对地址原样返回；相对上传路径补上后端服务域名。
  if (/^https?:\/\//i.test(imageUrl)) return imageUrl
  const apiOrigin = API_BASE.replace(/\/api\/?$/, '')
  return `${apiOrigin}${imageUrl.startsWith('/') ? imageUrl : `/${imageUrl}`}`
}

export async function fetchProducts(filters: ProductFilters): Promise<ProductListResponse> {
  // [商品列表页 / 搜索与筛选] 将数组筛选项编码为重复 query 参数，与后端 /api/products 对应。
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item))
    else if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return request<ProductListResponse>(`/products?${params}`, 'GET', undefined, {
    fallback: useI18n().t('Product loading failed'),
  })
}

export const fetchProductOptions = () =>
  request<ProductOptions>('/products/options', 'GET', undefined, {
    fallback: useI18n().t('Filter options failed to load'),
  })

export async function fetchOrderableProducts(): Promise<Product[]> {
  const pageSize = 48
  // 下单选择器需要跨页商品；复用目录请求，避免维护第二套商品列表协议。
  const loadPage = (page: number) =>
    fetchProducts({
      q: '',
      category: [],
      color: [],
      shipping: [],
      page,
      page_size: pageSize,
      sort: 'featured',
    })
  const firstPage = await loadPage(1)
  const pageCount = Math.ceil(firstPage.total / pageSize)
  const remainingPages = await Promise.all(
    Array.from({ length: Math.max(0, pageCount - 1) }, (_, index) => loadPage(index + 2)),
  )
  return [firstPage, ...remainingPages]
    .flatMap((page) => page.items)
    .filter(
      (product) =>
        // 白名单与后端可售规则一致，下架/归档商品即使有库存也不能供下单选择。
        product.stock > 0 && ['Active', 'Low stock', 'Out of stock'].includes(product.status),
    )
}

function createProductFormData(product: ProductWrite, image?: File): FormData {
  // [商品 API / 表单编码] 商品字段与图片一起作为 multipart/form-data 上传。
  const formData = new FormData()
  Object.entries(product).forEach(([key, value]) => formData.append(key, String(value)))
  if (image) formData.append('image', image)
  return formData
}

export const createProduct = (product: ProductWrite, image: File) =>
  request<Product>('/products', 'POST', createProductFormData(product, image), {
    fallback: useI18n().t('Product save failed'),
  })

export const updateProduct = (id: number, product: ProductWrite, image?: File) =>
  request<Product>(`/products/${id}`, 'PUT', createProductFormData(product, image), {
    fallback: useI18n().t('Product save failed'),
  })

export const deleteProduct = (id: number) =>
  request<void>(`/products/${id}`, 'DELETE', undefined, {
    fallback: useI18n().t('Product delete failed'),
  })
