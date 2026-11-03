<script setup lang="ts">
// 页面仅组合筛选、列表和分页；查询顺序及已读状态由功能 composable 管理。
import OperationsShell from '../app/layouts/OperationsShell.vue'
import AppPagination from '../shared/components/AppPagination.vue'
import { dateTime } from '../shared/commerce'
import { useNotifications } from '../features/notifications/composables/useNotifications'
import { ChevronDown, CheckCheck, RefreshCw } from '@lucide/vue'
import { computed } from 'vue'
defineProps<{ username: string }>()
const {
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
} = useNotifications()
const pageUnreadCount = computed(() => items.value.filter((item) => !item.read_at).length)
</script>

<template>
  <OperationsShell active-page="notifications" title="消息待办" :username="username">
    <div class="ops-heading notification-heading">
      <div>
        <h1>消息待办中心</h1>
        <p>红色角标仅统计未读消息；已读待办仍可继续处理。</p>
      </div>
      <div class="section-actions">
        <button class="ops-button" :disabled="loading || saving" @click="load">
          <RefreshCw :size="15" />刷新
        </button>
        <button
          class="ops-button primary"
          :disabled="loading || saving || unreadCount === 0"
          title="将所有分类、所有分页中的当前待办标为已读"
          @click="markAllRead"
        >
          <CheckCheck :size="16" />全部标为已读
        </button>
      </div>
    </div>
    <div class="notification-summary" aria-live="polite">
      <span
        >当前待办 <strong>{{ activeCount }}</strong></span
      >
      <button type="button" :disabled="loading || saving || unreadCount === 0" @click="showUnread">
        未读 <strong>{{ unreadCount }}</strong>
      </button>
      <span v-if="!loading && unreadCount === 0" class="notification-all-read">全部消息已读</span>
    </div>
    <form class="notification-filters" aria-label="消息筛选" @submit.prevent>
      <label class="notification-filter">
        <span>消息类型</span
        ><span class="notification-select"
          ><select v-model="category" :disabled="saving" @change="applyFilters">
            <option value="all">全部类型</option>
            <option value="stock">库存预警</option>
            <option value="shipment">超时未发货</option>
            <option value="aftersale">售后处理中</option></select
          ><ChevronDown :size="16" aria-hidden="true" /></span
      ></label>
      <label class="notification-filter">
        <span>阅读状态</span
        ><span class="notification-select"
          ><select v-model="state" :disabled="saving" @change="applyFilters">
            <option value="all">全部</option>
            <option value="unread">未读</option>
            <option value="read">已读</option></select
          ><ChevronDown :size="16" aria-hidden="true" /></span
      ></label>
      <button
        type="button"
        class="ops-button notification-page-read"
        :disabled="loading || saving || pageUnreadCount === 0"
        title="只标记当前页可见的未读消息，不影响其他分页或分类"
        @click="markRead(items.filter((item) => !item.read_at).map((item) => item.key))"
      >
        当前页标为已读<span v-if="pageUnreadCount">（{{ pageUnreadCount }}）</span>
      </button>
    </form>
    <p
      v-if="!loading && !error && unreadCount > 0 && pageUnreadCount === 0"
      class="notification-scope-hint"
    >
      当前页没有未读消息，其他分页或分类仍有 {{ unreadCount }} 条未读。<button
        class="text-action"
        :disabled="saving"
        @click="showUnread"
      >
        查看全部未读
      </button>
    </p>
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load">重试</button>
    </p>
    <p v-if="loading" class="module-empty">正在加载待办…</p>
    <div v-else-if="items.length" class="notification-list">
      <article
        v-for="item in items"
        :key="item.key"
        class="module-card notification-item"
        :class="{ unread: !item.read_at }"
      >
        <div>
          <span class="notification-state">{{
            item.read_at ? '已读 · 待处理' : '未读 · 待处理'
          }}</span>
          <h2>{{ item.title }}</h2>
          <p>{{ item.subject }}</p>
          <small>{{ dateTime(item.occurred_at) }}</small>
        </div>
        <div class="section-actions">
          <button
            v-if="!item.read_at"
            class="ops-button"
            :disabled="saving"
            @click="markRead([item.key])"
          >
            标记已读</button
          ><a class="ops-button primary" :href="item.href">前往处理</a>
        </div>
      </article>
    </div>
    <p v-else-if="!error" class="module-empty">当前条件下没有待办。</p>
    <AppPagination
      :page="page"
      :page-size="20"
      :page-count="pageCount"
      :total="total"
      :loading="loading || saving"
      @change="changePage"
    />
  </OperationsShell>
</template>
