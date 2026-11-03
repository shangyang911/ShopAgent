<script setup lang="ts">
import { LogOut } from '@lucide/vue'
import AppSidebar from '../../shared/components/AppSidebar.vue'
import { logout } from '../../features/auth/api/authApi'
import { ref } from 'vue'
import type { NavigationPage } from '../../shared/navigation/pages'
defineProps<{
  activePage: NavigationPage
  title: string
  username: string
}>()
const error = ref('')
async function signOut() {
  try {
    await logout()
    window.location.assign('/login')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '退出失败'
  }
}
</script>
<template>
  <main class="app-frame operations-frame">
    <AppSidebar :active-page="activePage" />
    <section class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          <span>Corelystics 商店</span><span>/</span><strong>{{ title }}</strong>
        </div>
        <div class="shipping-topbar-spacer"></div>
        <div class="top-actions">
          <span class="signed-in-user">{{ username }}</span
          ><button class="icon-button" aria-label="退出登录" @click="signOut">
            <LogOut :size="18" />
          </button>
        </div>
      </header>
      <div class="operations-content">
        <p v-if="error" class="ops-error" role="alert">{{ error }}</p>
        <slot />
      </div>
    </section>
  </main>
</template>
