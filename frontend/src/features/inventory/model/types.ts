export interface InventoryRow {
  product_id: number
  name: string
  category: string
  available_quantity: number
  reserved_quantity: number
  on_hand_quantity: number
  monitor_enabled: boolean
  threshold_override: number | null
  effective_threshold: number
  severity: string
}

export interface Movement {
  id: number
  product_id: number
  product_name_snapshot: string
  movement_type: string
  available_before: number
  available_delta: number
  available_after: number
  reserved_delta: number
  on_hand_delta: number
  reference_type: string
  reference_id: number
  reference_number: string
  actor_name_snapshot: string
  reason: string | null
  occurred_at: string
}

export interface Alert {
  id: number
  product_id: number
  product_name_snapshot: string
  severity: string
  alert_status: string
  current_available: number | null
  available_at_trigger: number
  first_triggered_at: string
  resolved_at: string | null
  acknowledged_at: string | null
  acknowledged_by: string | null
  close_reason: string | null
}

export interface Inbound {
  id: number
  receipt_number: string
  status: string
  remark: string
  created_by: string
  created_at: string
  confirmed_by: string | null
  confirmed_at: string | null
  items: {
    product_id: number
    sku_id?: number | null
    product_name_snapshot: string
    quantity: number
  }[]
}
