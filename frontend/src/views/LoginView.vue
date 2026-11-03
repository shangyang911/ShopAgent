<script setup lang="ts">
// [登录页] 展示管理员账号登录表单；认证请求由 features/auth/api/authApi 负责。
import { ref } from 'vue'
import { useI18n } from '../shared/i18n'
import { login } from '../features/auth/api/authApi'

const { t } = useI18n()
const username = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

async function submitLogin() {
  error.value = ''
  submitting.value = true
  try {
    await login(username.value.trim(), password.value)
    // 登录后重新恢复会话，进入经营概览。
    window.location.assign('/')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Sign in failed')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <!-- [登录页] 独立页面视图：收集管理员凭据并反馈登录结果。 -->
  <main class="auth-page">
    <section class="auth-card">
      <a class="auth-brand" href="/" aria-label="Corelystics">C</a>
      <p class="auth-eyebrow">CORELYSTICS STORE</p>
      <h1>{{ t('Welcome back') }}</h1>
      <p class="auth-description">{{ t('Sign in to manage your store.') }}</p>

      <form class="auth-form" @submit.prevent="submitLogin">
        <label>
          <span>{{ t('Username') }}</span>
          <input
            v-model="username"
            name="username"
            autocomplete="username"
            required
            minlength="3"
            maxlength="50"
          />
        </label>
        <label>
          <span>{{ t('Password') }}</span>
          <input
            v-model="password"
            name="password"
            type="password"
            autocomplete="current-password"
            required
            minlength="6"
            maxlength="128"
          />
        </label>
        <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
        <button class="auth-submit" type="submit" :disabled="submitting">
          {{ submitting ? t('Signing in…') : t('Sign in') }}
        </button>
      </form>
    </section>
  </main>
</template>
