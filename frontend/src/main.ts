import './styles/business-panels.css'
// [前端入口] 初始化商品管理站点，设置当前语言元信息后挂载根组件。
import { createApp } from 'vue'
import './styles/main.css'
import './styles/operations.css'
import './styles/operational-features.css'
import './styles/glass-theme.css'
import './styles/navigation.css'
import App from './App.vue'
import { getCurrentLocale } from './shared/i18n'
import { loadAmapApi } from './features/shipping/config/loadAmapApi'
import { installRandomUUID } from './shared/randomUUID'

installRandomUUID()

// 保留高德能力：直达发货页时提前下载 SDK；地图加载与发货流程各自独立。
if (window.location.pathname === '/shipping') void loadAmapApi().catch(() => {})

document.documentElement.lang = getCurrentLocale() === 'zh-CN' ? 'zh-CN' : 'en'
document.title =
  getCurrentLocale() === 'zh-CN' ? 'Corelystics 商店 · 商品' : 'Corelystics Store · Products'

createApp(App).mount('#app')
