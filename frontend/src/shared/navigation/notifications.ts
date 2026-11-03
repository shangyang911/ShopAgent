// 共享导航只依赖注入契约，不反向导入 notifications 业务模块。
import type { InjectionKey, Ref } from 'vue'
export interface NotificationNavigation {
  unreadCount: Readonly<Ref<number>>
  refresh: () => Promise<void>
  setUnreadCount: (count: number) => void
}
export const notificationNavigationKey: InjectionKey<NotificationNavigation> =
  Symbol('notification-navigation')
