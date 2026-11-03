<script setup lang="ts">
// [全局侧栏] 根据统一导航表生成图标与文字；当前入口由页面传入，子页可独立高亮。
import {
  Bell,
  Activity,
  BookOpen,
  BotMessageSquare,
  Box,
  ClipboardList,
  Heart,
  LayoutDashboard,
  Package,
  Sparkles,
  Truck,
  Users,
} from '@lucide/vue'
import { inject } from 'vue'
import { useI18n } from '../i18n'
import { notificationNavigationKey } from '../navigation/notifications'
import { navigationPages, type NavigationPage } from '../navigation/pages'
withDefaults(defineProps<{ activePage?: NavigationPage }>(), { activePage: 'products' })
const { t } = useI18n()
const notifications = inject(notificationNavigationKey, undefined)
// 读取App持有的角标实例，侧栏挂载时不另外创建轮询请求。
const icons = {
  // 图标键对应导航表；具体组件仅在展示层引用，保持共享导航数据不依赖页面。
  observability: Activity,
  overview: LayoutDashboard,
  products: Package,
  inventory: Box,
  orders: ClipboardList,
  customers: Users,
  shipping: Truck,
  copilot: Sparkles,
  agent: BotMessageSquare,
  knowledge: BookOpen,
  notifications: Bell,
  favorites: Heart,
}
</script>
<template>
  <aside class="rail">
    <a class="brand-mark" href="/" :aria-label="t('Corelystics home')" :title="t('Overview')"
      ><span>✦</span></a
    >
    <nav class="rail-nav" :aria-label="t('Main navigation')">
      <a
        v-for="page in navigationPages"
        :key="page.id"
        :href="page.href"
        :class="{ selected: activePage === page.id, 'rail-group-start': page.id === 'copilot' }"
        :title="t(page.label)"
        :aria-label="t(page.label)"
        :aria-current="activePage === page.id ? 'page' : undefined"
      >
        <component :is="icons[page.icon]" aria-hidden="true" />
        <span class="rail-label">{{ t(page.label) }}</span>
        <span
          v-if="page.id === 'notifications' && notifications && notifications.unreadCount.value > 0"
          class="nav-notification-count"
          >{{
            notifications.unreadCount.value > 99 ? '99+' : notifications.unreadCount.value
          }}</span
        >
      </a>
    </nav>
    <div class="rail-bottom"><div class="avatar" aria-label="Corelystics">CL</div></div>
  </aside>
</template>
