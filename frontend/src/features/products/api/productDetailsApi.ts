import { request } from '../../../shared/api/httpClient'
import type { ProductDetail } from '../model/types'

export const fetchProductDetail = (id: number) => request<ProductDetail>(`/products/${id}`)

export const saveProductDetail = (
  id: number,
  body: {
    expected_revision: number
    gallery: { id: number; alt: string; color: string }[]
    description: { type: 'text' | 'image'; text?: string; image_id?: number | null }[]
    parameters: { name: string; value: string }[]
  },
) => request<ProductDetail>(`/products/${id}/detail`, 'PUT', body)

export const uploadProductImage = (
  id: number,
  image: File,
  revision: number,
  role: 'gallery' | 'detail' = 'gallery',
) => {
  const data = new FormData()
  data.append('image', image)
  data.append('expected_revision', String(revision))
  data.append('role', role)
  return request<ProductDetail>(`/products/${id}/images`, 'POST', data)
}

export const saveProductSKU = (
  id: number,
  body: {
    code: string
    color: string
    size: string
    price: string
    active: boolean
    expected_revision?: number
  },
  skuId?: number,
) =>
  request<ProductDetail>(
    `/products/${id}/skus${skuId == null ? '' : `/${skuId}`}`,
    skuId == null ? 'POST' : 'PUT',
    body,
  )

export const transferProductStock = (
  id: number,
  body: {
    from_sku_id: number
    to_sku_id: number
    quantity: number
    reason: string
    idempotency_key: string
  },
) => request<ProductDetail>(`/products/${id}/sku-stock-transfer`, 'POST', body)
