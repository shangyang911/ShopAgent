// 数量均为整数；delta 为有符号差额，counted_on_hand 为含预留的实盘在库数。
export interface StockAdjustment {
  id: number
  kind: 'count' | 'damage' | 'correction'
  status: 'draft' | 'confirmed' | 'cancelled'
  reason: string
  source_inbound_id: number | null
  // 创建与确认分别留人和时间，草稿不代表已经发生库存调整。
  created_by: string
  created_at: string
  confirmed_by: string | null
  confirmed_at: string | null
  items: {
    product_id: number
    product_name: string
    available_before: number
    reserved_before: number
    // 盘点填实物数量，报损和入库更正为 null；不能把 null 显示为实盘零件。
    counted_on_hand: number | null
    available_delta: number
  }[]
}
