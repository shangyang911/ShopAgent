// 页面和角标共用接口，避免维护两套未读统计口径。
import { request } from '../../../shared/api/httpClient'
import { query } from '../../../shared/api/query'
import type { NotificationCategory, NotificationPage, NotificationState } from '../model/types'

export const fetchNotifications = (
  category: NotificationCategory = 'all',
  state: NotificationState = 'all',
  page = 1,
  pageSize = 20,
) =>
  request<NotificationPage>(
    `/notifications?${query({ category, state, page, page_size: pageSize })}`,
  )
export const markNotificationsRead = (keys: string[]) =>
  request<void>('/notifications/read', 'POST', { keys })
// 全部已读由后端一次处理所有分类和分页，不循环提交当前可见的 20 条。
export const markAllNotificationsRead = () => request<void>('/notifications/read-all', 'POST')
