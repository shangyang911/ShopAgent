import { request } from '../../../shared/api/httpClient'
import type { Page } from '../../../shared/api/types'
import type { StockAdjustment } from '../model/adjustments'

// 草稿与确认分开调用，列表刷新不会产生库存副作用。
export const fetchAdjustments = (page = 1) =>
  request<Page<StockAdjustment>>(`/inventory/adjustments?page=${page}`)
export const createAdjustment = (payload: object) =>
  request<StockAdjustment>('/inventory/adjustments', 'POST', payload)
// 确认由服务端检查冻结版本；重复请求返回已确认单据，不重复生成流水。
export const confirmAdjustment = (id: number) =>
  request<StockAdjustment>(`/inventory/adjustments/${id}/confirm`, 'POST')
export const cancelAdjustment = (id: number) =>
  request<StockAdjustment>(`/inventory/adjustments/${id}/cancel`, 'POST')
