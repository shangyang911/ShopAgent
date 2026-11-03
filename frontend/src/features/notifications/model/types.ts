// 消息来源复用真实业务 ID；已读状态与业务是否处理完成互不替代。
export type NotificationCategory = 'all' | 'stock' | 'shipment' | 'aftersale'
export type NotificationState = 'all' | 'unread' | 'read'
export interface NotificationItem {
  key: string
  category: Exclude<NotificationCategory, 'all'>
  reference_id: number
  subject: string
  title: string
  occurred_at: string
  read_at: string | null
  href: string
}
export interface NotificationPage {
  items: NotificationItem[]
  total: number
  unread_count: number
  active_count: number
  page: number
  page_size: number
}
