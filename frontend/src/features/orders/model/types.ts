import type { ShipmentRecord } from '../../shipping/model/shipping'

export type OrderStatus =
  | 'pending_payment'
  | 'paid_pending_shipment'
  | 'in_delivery'
  | 'completed'
  | 'cancelled'
  | 'refunded'

// 查询视图允许售后与履约重叠，售后处理中不是新的履约状态。
export type OrderFilterStatus = OrderStatus | 'aftersale_pending'

export interface OrderItem {
  id: number
  product_id: number | null
  product_name: string
  sku_id?: number | null
  sku_code?: string | null
  sku_label?: string | null
  quantity: number
  unit_price: string
  line_total: string
}

export interface OrderRecord {
  id: number
  order_number: string
  customer_id: number
  address_id: number | null
  status: OrderStatus
  total: string
  recipient_name: string
  recipient_phone: string
  country: string
  province: string
  city: string
  district: string
  address_detail: string
  longitude: number | null
  latitude: number | null
  created_at: string
  paid_at: string | null
  dispatch_started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  created_time_verified: boolean
  print_request_count: number
  refunded_amount?: string
  aftersale_pending?: boolean
  logistics_summary?: { package_count: number; signed_count: number; summary: string } | null
  subtotal: string
  shipping_fee: string
  note: string | null
  items: OrderItem[]
  shipment: ShipmentRecord | null
}

export interface OrderListResponse {
  items: OrderRecord[]
  total: number
  page: number
  page_size: number
  status_counts: Partial<Record<OrderFilterStatus, number>>
}

export interface OrderCreatePayload {
  customer_id: number
  address_id: number
  items: { product_id: number; sku_id?: number; quantity: number }[]
  shipping_fee: string | number
  note: string | null
}

export interface OrderFilters {
  keyword?: string
  statuses?: OrderFilterStatus[]
  time_field?: 'created_at' | 'paid_at' | 'completed_at'
  start_at?: string
  end_at?: string
  as_of?: string
  min_total?: string
  max_total?: string
  print_request_status?: string
  overdue?: boolean
  unknown_time?: boolean
  sort?: 'created_desc' | 'created_asc'
}

export interface TimelineEvent {
  id: number
  sequence: number
  event_type: string
  occurred_at: string | null
  recorded_at: string
  actor_name_snapshot: string
  summary: string
  reason: string | null
  from_status: string | null
  to_status: string | null
  correlation_id?: string
  changed_fields?: Record<string, { before: unknown; after: unknown }>
  details: {
    movement_ids?: number[]
    receipt_id?: number
    amount?: string
    print_job_id?: number
    document_id?: number
    corrects_event_id?: number
    corrects_sequence?: number
    layout?: 'orders' | 'summary'
  }
}

export interface PrintDocument {
  id: number
  order_id: number
  job_id: number
  attempt_no: number
  document_revision: number
  document_hash: string
}
