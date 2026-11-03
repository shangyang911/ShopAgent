<script setup lang="ts">
import { computed, onMounted, provide, ref } from 'vue'
import { useNotificationBadge } from './features/notifications/composables/useNotificationBadge'
import { notificationNavigationKey } from './shared/navigation/notifications'
import { fetchAuthStatus, fetchCurrentUser } from './features/auth/api/authApi'
import type { AuthUser } from './features/auth/model/types'
import { useAppRouter } from './router'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'

const page = ref<'loading' | 'authenticated' | 'login' | 'register' | 'error'>('loading')
const user = ref<AuthUser | null>(null)
// 角标由应用统一维护，导航通过共享契约读取，不参与业务请求。
provide(notificationNavigationKey, useNotificationBadge(computed(() => user.value !== null)))
const startupError = ref('')
const { currentRoute, routeKey, syncRoute } = useAppRouter(computed(() => user.value !== null))
const currentView = computed(() =>
  page.value === 'authenticated' ? currentRoute.value.component : null,
)

onMounted(async () => {
  try {
    const [currentUser, authStatus] = await Promise.all([fetchCurrentUser(), fetchAuthStatus()])
    user.value = currentUser
    if (currentUser) {
      syncRoute()
      page.value = 'authenticated'
    } else if (authStatus.setup_required || window.location.pathname === '/register') {
      page.value = 'register'
    } else {
      page.value = 'login'
    }
  } catch (reason) {
    startupError.value =
      reason instanceof Error ? reason.message : 'Could not connect to the server.'
    page.value = 'error'
  }
})
</script>

<template>
  <!-- [页面分流 / 初始化中] 避免登录态确认前短暂显示受保护的商品后台。 -->
  <main v-if="page === 'loading'" class="auth-page"><p>Loading…</p></main>
  <main v-else-if="page === 'error'" class="auth-page">
    <p class="auth-error">{{ startupError }}</p>
  </main>
  <KeepAlive v-else-if="currentView" include="ShippingView">
    <component :is="currentView" :key="routeKey" :username="user?.username ?? ''" />
  </KeepAlive>
  <RegisterView v-else-if="page === 'register'" />
  <LoginView v-else />
</template>
