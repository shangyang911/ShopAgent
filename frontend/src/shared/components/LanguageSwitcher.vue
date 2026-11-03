<script setup lang="ts">
// [共享组件 / 全站语言切换] 在顶部工具栏显示当前语言，并切换全局 i18n 状态。
import { ChevronDown } from '@lucide/vue'
import { useI18n, type Locale } from '../i18n'

const { locale, setLocale } = useI18n()

function toggleLocale() {
  // [共享语言切换 / 点击处理] 在英文和简体中文之间切换全局语言状态。
  const next: Locale = locale.value === 'en' ? 'zh-CN' : 'en'
  setLocale(next)
}
</script>

<template>
  <button
    class="language"
    type="button"
    :aria-label="locale === 'en' ? 'Switch language to Chinese' : 'Switch language to English'"
    :title="locale === 'en' ? '切换为中文' : 'Switch to English'"
    @click="toggleLocale"
  >
    <span class="flag" aria-hidden="true">{{ locale === 'en' ? '🇬🇧' : '🇨🇳' }}</span>
    {{ locale === 'en' ? 'EN' : '中文' }}
    <ChevronDown :size="13" />
  </button>
</template>
