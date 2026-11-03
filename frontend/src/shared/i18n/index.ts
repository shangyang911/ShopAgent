// [全站语言状态] 为导航、商品列表页及共享组件提供同一份中英文状态与翻译函数。
import { computed, ref } from 'vue'
import { messages, type Locale } from './messages'

const STORAGE_KEY = 'corelystics.locale'

function getInitialLocale(): Locale {
  // [全站语言 / 初始值] 优先恢复浏览器保存的语言；读取受限时回退英文。
  try {
    return localStorage.getItem(STORAGE_KEY) === 'zh-CN' ? 'zh-CN' : 'en'
  } catch {
    return 'en'
  }
}

const currentLocale = ref<Locale>(getInitialLocale())
const localeMessages: Record<Locale, Record<string, string>> = messages

export function getCurrentLocale(): Locale {
  // [全站语言 / 当前值] 提供非组件代码读取当前 locale 的入口。
  return currentLocale.value
}

export function setLocale(locale: Locale) {
  // [全站语言切换] 保存语言选择，并同步浏览器标签页的 lang 与标题。
  currentLocale.value = locale
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // Language switching still works when browser storage is unavailable.
  }
  document.documentElement.lang = locale === 'zh-CN' ? 'zh-CN' : 'en'
  document.title = locale === 'zh-CN' ? 'Corelystics 商店 · 商品' : 'Corelystics Store · Products'
}

export function useI18n() {
  // [全站语言 / 组件 API] 提供响应式语言、翻译函数和切换函数。
  const locale = computed(() => currentLocale.value)
  function t(key: string): string {
    // 先查当前语言，缺失时回退英文，再回退到原始键名。
    return localeMessages[currentLocale.value][key] ?? localeMessages.en[key] ?? key
  }
  function translateValue(value: string): string {
    // 商品分类、状态、颜色等值与页面文案共用同一翻译表。
    return t(value)
  }
  return { locale, t, translateValue, setLocale }
}

export type { Locale }
