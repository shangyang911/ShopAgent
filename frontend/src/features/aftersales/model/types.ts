// 资金事实、验收事实与履约状态独立，避免用一个 refunded 状态解释全部售后。
export interface ReturnLine {
  order_item_id: number
  product_name: string
  quantity: number
  accepted_quantity: number | null
  // null 表示尚未验收，0 表示验收后确认为零，两者不能互换。
  damaged_quantity: number | null
}
export interface AfterSale {
  id: number
  kind: 'refund' | 'return_refund'
  status: 'open' | 'closed' | 'cancelled'
  requested_amount: string
  reason: string
  // 每次写入提升版本，页面提交时回传，拒绝陈旧表单。
  revision: number
  inspected_at: string | null
  inspected_by: string | null
  inspection_note: string | null
  items: ReturnLine[]
  refunds: {
    id: number
    amount: string
    reference: string
    occurred_at: string
    recorded_by: string
  }[]
}
export interface AfterSaleSnapshot {
  order_id: number
  confirmed_paid: string
  // 金额保留十进制字符串；展示可格式化，累计与限额以服务端 Decimal 为准。
  refunded_amount: string
  refundable_amount: string
  payment_verified: boolean
  items: AfterSale[]
}
