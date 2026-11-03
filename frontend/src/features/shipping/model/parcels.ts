export interface LabelTask {
  id: number
  package_no: number
  status: 'requesting' | 'unknown' | 'issued' | 'failed'
  carrier_name: string
  tracking_number: string | null
  label_url: string | null
  error: string | null
}

export interface ParcelRecord {
  package_no: number
  carrier_name: string
  tracking_number: string
  remark: string
  freight: {
    actual_cost: string
    source: string
    revision: number
    updated_by: string
    updated_at: string
  } | null
  label: LabelTask | null
}

export interface PackageSnapshot {
  // 批量预览读取最新售后状态，防止沿用选单时尚未暂停的旧数据。
  aftersale_pending?: boolean
  order_id: number
  revision: number
  items: ParcelRecord[]
  actual_cost: string
  unsettled_packages: number
  label_tasks: LabelTask[]
}

export interface FreightReport {
  order_count: number
  package_count: number
  settled_packages: number
  unsettled_packages: number
  actual_cost: string
  customer_shipping_fee: string
  complete: boolean
  groups: {
    carrier: string
    carrier_name: string
    packages: number
    settled_packages: number
    unsettled_packages: number
    actual_cost: string
  }[]
}
