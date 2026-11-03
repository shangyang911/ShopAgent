import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { notificationNavigationKey } from '../../../shared/navigation/notifications'
import {
  fetchNotifications,
  markNotificationsRead,
  markAllNotificationsRead,
} from '../api/notificationsApi'
import type { NotificationCategory, NotificationItem, NotificationState } from '../model/types'

export function useNotifications() {
  const navigation = inject(notificationNavigationKey, undefined)
  const category = ref<NotificationCategory>('all')
  const state = ref<NotificationState>('all')
  const page = ref(1)
  const total = ref(0)
  const activeCount = ref(0)
  const unreadCount = ref(0)
  const items = ref<NotificationItem[]>([])
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 20)))
  let generation = 0
  let disposed = false

  async function load() {
    if (disposed) return
    const id = ++generation
    loading.value = true
    error.value = ''
    try {
      const result = await fetchNotifications(category.value, state.value, page.value)
      if (id !== generation) return
      total.value = result.total
      activeCount.value = result.active_count
      unreadCount.value = result.unread_count
      navigation?.setUnreadCount(result.unread_count)
      if (page.value > pageCount.value) {
        page.value = pageCount.value
        await load()
        return
      }
      items.value = result.items
    } catch (reason) {
      if (id === generation) error.value = reason instanceof Error ? reason.message : '待办加载失败'
    } finally {
      if (id === generation) loading.value = false
    }
  }
  function applyFilters() {
    page.value = 1
    void load()
  }
  function changePage(value: number) {
    page.value = value
    void load()
  }
  function showUnread() {
    category.value = 'all'
    state.value = 'unread'
    applyFilters()
  }
  async function updateReadStatus(write: () => Promise<void>) {
    if (saving.value || disposed) return
    saving.value = true
    error.value = ''
    try {
      await write()
      if (disposed) return
      // 成功加载列表时直接同步它携带的全局未读数，减少一次独立计数请求。
      await load()
      if (error.value && !disposed) await navigation?.refresh()
    } catch (reason) {
      if (!disposed) error.value = reason instanceof Error ? reason.message : '标记已读失败'
    } finally {
      saving.value = false
    }
  }
  async function markRead(keys: string[]) {
    if (keys.length) await updateReadStatus(() => markNotificationsRead(keys))
  }
  async function markAllRead() {
    await updateReadStatus(markAllNotificationsRead)
  }
  onMounted(load)
  onBeforeUnmount(() => {
    disposed = true
    generation++
  })
  return {
    category,
    state,
    page,
    total,
    activeCount,
    unreadCount,
    items,
    loading,
    saving,
    error,
    pageCount,
    load,
    applyFilters,
    changePage,
    markRead,
    markAllRead,
    showUnread,
  }
}
