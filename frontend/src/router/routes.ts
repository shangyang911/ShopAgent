import DashboardView from '../views/DashboardView.vue'
import ProductsView from '../views/ProductsView.vue'
import ProductDetailView from '../views/ProductDetailView.vue'
import InventoryView from '../views/InventoryView.vue'
import OrdersView from '../views/OrdersView.vue'
import CustomersView from '../views/CustomersView.vue'
import ShippingView from '../views/ShippingView.vue'
import AgentView from '../views/AgentView.vue'
import KnowledgeView from '../views/KnowledgeView.vue'
import ObservabilityView from '../views/ObservabilityView.vue'
import FavoritesView from '../views/FavoritesView.vue'
import NotificationsView from '../views/NotificationsView.vue'

// 管理页面注册表；登录与首次注册由 App.vue 的会话状态决定。
export const routes = [
  { path: '/', name: 'dashboard', component: DashboardView },
  { path: '/products', name: 'products', component: ProductsView },
  { path: '/favorites', name: 'favorites', component: FavoritesView },
  { path: '/notifications', name: 'notifications', component: NotificationsView },
  { path: '/products/detail', name: 'product-detail', component: ProductDetailView },
  { path: '/inventory', name: 'inventory', component: InventoryView },
  { path: '/orders', name: 'orders', component: OrdersView },
  { path: '/customers', name: 'customers', component: CustomersView },
  { path: '/shipping', name: 'shipping', component: ShippingView },
  { path: '/agent', name: 'agent', component: AgentView },
  { path: '/knowledge', name: 'knowledge', component: KnowledgeView },
  { path: '/ai-observability', name: 'observability', component: ObservabilityView },
] as const
