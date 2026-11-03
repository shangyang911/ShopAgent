import type { OrderRecord } from '../../orders/model/types'

import type { TrackingSnapshot } from './tracking'

export interface ShipmentRecord {
  order_id: number
  carrier_name: string
  tracking_number: string
  remark: string
  revision: number
  historical: boolean
  updated_by: string
  updated_at: string
  dispatched_by: string | null
  dispatched_at: string | null
  tracking?: Pick<
    TrackingSnapshot,
    | 'status'
    | 'latest_event_at'
    | 'latest_description'
    | 'last_success_at'
    | 'last_attempt_at'
    | 'next_sync_at'
    | 'error_code'
    | 'error_message'
  > | null
}

export interface ShipmentWrite {
  carrier_name: string
  tracking_number: string
  remark: string
  expected_revision: number
}

export interface ShippingWorkbench {
  items: OrderRecord[]
  total: number
  page: number
  page_size: number
  counts: Partial<Record<'paid_pending_shipment' | 'in_delivery' | 'completed', number>>
}

export interface ShippingMapSettings {
  center: [number, number]
  zoom: number
}

export interface DeliveryRoute {
  order_id: number
  order_number: string
  origin: [number, number]
  destination: [number, number]
  distance_meters: number
  duration_seconds: number
  polyline: [number, number][]
}
