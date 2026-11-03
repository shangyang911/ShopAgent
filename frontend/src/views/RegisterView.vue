<script setup lang="ts">
// [注册页] 首次部署时创建唯一的后台管理员；请求由 features/auth/api/authApi 负责。
import { onMounted, ref } from 'vue'
import { useI18n } from '../shared/i18n'
import { fetchAuthStatus, registerFirstAdmin } from '../features/auth/api/authApi'

const { t } = useI18n()
const username = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)
const setupRequired = ref(false)
const checkingSetup = ref(true)

onMounted(async () => {
  // [注册页 / 初始化检查] 后端决定是否仍允许创建首个管理员，前端状态不作为安全依据。
  try {
    setupRequired.value = (await fetchAuthStatus()).setup_required
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not check setup status')
  } finally {
    checkingSetup.value = false
  }
})

async function submitRegistration() {
  error.value = ''
  submitting.value = true
  try {
    await registerFirstAdmin(username.value.trim(), password.value)
    // [注册页 / 成功跳转] 首位管理员创建后已自动登录，返回后台首页。
    window.location.assign('/')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Registration failed')
    if (error.value.includes('already exists')) setupRequired.value = false
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <!-- [注册页] 独立页面视图：首次启动建立管理员，之后显示注册已关闭状态。 -->
  <main class="auth-page">
    <section class="auth-card">
      <a class="auth-brand" href="/" aria-label="Corelystics">C</a>
      <p class="auth-eyebrow">CORELYSTICS STORE</p>
      <template v-if="checkingSetup">
        <h1>{{ t('Checking setup…') }}</h1>
      </template>
      <template v-else-if="setupRequired">
        <h1>{{ t('Create administrator') }}</h1>
        <p class="auth-description">
          {{ t('Create the first account to secure your store dashboard.') }}
        </p>
        <form class="auth-form" @submit.prevent="submitRegistration">
          <label>
            <span>{{ t('Username') }}</span>
            <input
              v-model="username"
              name="username"
              autocomplete="username"
              required
              minlength="3"
              maxlength="50"
              pattern="[A-Za-z0-9_.\-]+"
              :title="t('Use letters, numbers, dots, dashes, or underscores.')"
            />
          </label>
          <label>
            <span>{{ t('Password') }}</span>
            <input
              v-model="password"
              name="new-password"
              type="password"
              autocomplete="new-password"
              required
              minlength="6"
              maxlength="128"
            />
          </label>
          <p class="auth-hint">{{ t('Use at least 6 characters.') }}</p>
          <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
          <button class="auth-submit" type="submit" :disabled="submitting">
            {{ submitting ? t('Creating account…') : t('Create administrator') }}
          </button>
        </form>
      </template>
      <template v-else>
        <h1>{{ t('Registration is closed') }}</h1>
        <p class="auth-description">
          {{ t('An administrator account already exists. Sign in to continue.') }}
        </p>
        <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
        <a class="auth-submit auth-link-button" href="/login">{{ t('Go to sign in') }}</a>
      </template>
    </section>
  </main>
</template>
