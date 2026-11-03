export interface Dashboard {
  period_start: string
  period_end: string
  effective_end: string
  as_of: string
  data_coverage_start: string
  comparison_start: string
  comparison_end: string
  timezone: string
  metrics: Record<
    string,
    {
      value: string | number | null
      comparison_value: string | number | null
      change_label: string
      reliability: string
    }
  >
  trend: {
    label: string
    start_at: string
    end_at: string
    confirmed_receipt_amount: string | null
    confirmed_receipt_order_count: number | null
    created_order_count: number | null
    completed_order_count: number | null
    reliability: string
  }[]
  todos: {
    pending_shipment: number
    overdue_shipment: number
    unknown_payment_time: number
    low_stock: number
    out_of_stock: number
  }
}
