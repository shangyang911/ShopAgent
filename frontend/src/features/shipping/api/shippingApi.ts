import type {
  ShipmentRecord,
  ShipmentWrite,
  ShippingWorkbench,
  ShippingMapSettings,
  DeliveryRoute,
} from '../model/shipping'

import { request } from '../../../shared/api/httpClient'
import type { OrderRecord } from '../../orders/model/types'

export const fetchShippingWorkbench = (params: {
  status?: string
  keyword: string
  page: number
  page_size: number
}) =>
  request<ShippingWorkbench>('/shipping/workbench', 'GET', undefined, {
    params,
    fallback: '发货列表加载失败',
  })
export const saveShipment = (id: number, payload: ShipmentWrite) =>
  request<ShipmentRecord>(`/shipping/orders/${id}/shipment`, 'PUT', payload, {
    fallback: '快递信息保存失败',
  })
export const dispatchShipment = (id: number, revision: number, key: string) =>
  request<OrderRecord>(
    `/shipping/orders/${id}/dispatch`,
    'POST',
    { expected_revision: revision, idempotency_key: key },
    { fallback: '交接出库失败' },
  )
export const fetchShippingMapSettings = () => request<ShippingMapSettings>('/shipping/map-settings')
export const fetchDeliveryRoute = (id: number) =>
  request<DeliveryRoute>(`/shipping/orders/${id}/route`)
