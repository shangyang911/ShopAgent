export interface TrackingService {
  // 服务能力和同步间隔的公开配置；这里不包含快递100账号凭证。
  provider: string
  provider_name: string
  configured: boolean
  auto_sync: boolean
  interval_minutes: number
}

export interface TrackingEvent {
  id: number
  occurred_at: string | null // 平台节点实际发生时间；无法解析时保留 null，不拿记录时间代替。
  raw_time: string
  description: string
  location: string | null
  recorded_at: string // 本系统保存该节点的时间，用于区分“何时发生”和“何时接入”。
}

export interface TrackingSnapshot {
  // 一次接口返回的完整页面快照；status 属于物流进度，不是订单的完成状态。
  order_id: number
  order_number: string
  service: TrackingService
  carrier_name: string | null
  tracking_number: string | null
  eligible: boolean // 是否已交接或属于历史发货记录；它与服务是否配置是两个独立条件。
  status: string
  status_name: string
  last_success_at: string | null // 最近一次取得有效平台响应的时间，失败查询不会覆盖它。
  last_attempt_at: string | null
  latest_event_at: string | null
  latest_description: string | null
  next_sync_at: string | null
  error_code: string | null
  error_message: string | null
  syncing: boolean
  stale: boolean // 提醒界面按“上次记录，当前待核实”表述，不能宣称实时进度。
  events: TrackingEvent[]
  event_total: number
  events_truncated: boolean
  notice: string
  sync_result?: 'updated' | 'cached' | 'failed'
}
