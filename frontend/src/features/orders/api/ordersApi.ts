import type {
  OrderStatus,
  OrderFilterStatus,
  OrderRecord,
  OrderListResponse,
  OrderCreatePayload,
  OrderFilters,
} from '../model/types'

// [订单模块 / API] 订单列表和详情由订单功能统一负责读取。
import { request } from '../../../shared/api/httpClient'

export async function fetchOrders(
  status?: OrderFilterStatus,
  page = 1,
  pageSize = 20,
  filters: OrderFilters = {},
): Promise<OrderListResponse> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (status) query.set('status', status)
  Object.entries(filters).forEach(([key, value]) => {
    if (Array.isArray(value)) value.forEach((item) => query.append(key, item))
    else if (value !== undefined && value !== '' && value !== false) query.set(key, String(value))
  })
  return request<OrderListResponse>(`/orders?${query}`, 'GET', undefined, {
    fallback: '订单列表加载失败',
  })
}

export const fetchOrder = (id: number) =>
  request<OrderRecord>(`/orders/${id}`, 'GET', undefined, { fallback: '订单详情加载失败' })

export const createOrder = (payload: OrderCreatePayload, idempotencyKey?: string) =>
  request<OrderRecord>('/orders', 'POST', payload, {
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {},
    fallback: '创建订单失败',
  })

export const updateOrderStatus = (
  id: number,
  status: OrderStatus,
  reason?: string,
  idempotencyKey?: string,
) =>
  request<OrderRecord>(`/orders/${id}/status`, 'PATCH', {
    status,
    reason,
    idempotency_key: idempotencyKey,
  })
