import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { fetchNotifications } from '../api/notificationsApi'

// 由 App 持有唯一角标实例；页面切换不会创建新的轮询器。
export function useNotificationBadge(authenticated: Readonly<Ref<boolean>>) {
  const unreadCount = ref(0)
  let generation = 0
  let timer: ReturnType<typeof setInterval> | undefined
  function setUnreadCount(count: number) {
    // 列表和已读操作的新结果优先于正在途中的旧轮询，禁止旧角标重新出现。
    generation++
    unreadCount.value = count
  }
  async function refresh() {
    const id = ++generation
    if (!authenticated.value) {
      unreadCount.value = 0
      return
    }
    try {
      const result = await fetchNotifications('all', 'all', 1, 1)
      if (id === generation) unreadCount.value = result.unread_count
    } catch {
      // 网络抖动保留上次角标，具体错误由消息页展示。
    }
  }
  watch(
    authenticated,
    () => {
      void refresh()
    },
    { immediate: true },
  )
  onMounted(() => {
    timer = setInterval(() => {
      void refresh()
    }, 60000)
    window.addEventListener('focus', refresh)
  })
  onBeforeUnmount(() => {
    generation++
    clearInterval(timer)
    window.removeEventListener('focus', refresh)
  })
  return { unreadCount, refresh, setUnreadCount }
}
