import type { CustomerRecord } from './types'
import type { OrderStatus } from '../../orders/model/types'
export interface CustomerProfile {
  tags: string[]
  note: string
  revision: number
}
export interface CustomerDetail {
  customer: CustomerRecord
  summary: {
    order_count: number
    paid_order_count: number
    confirmed_amount: string
    refunded_amount: string
    net_confirmed_amount: string
    last_purchase_at: string | null
  }
  profile: CustomerProfile
  common_products: { product_name: string; quantity: number }[]
  orders: {
    items: {
      id: number
      order_number: string
      status: OrderStatus
      total: string
      created_at: string | null
      paid_at: string | null
    }[]
    total: number
    page: number
    page_size: number
  }
}
