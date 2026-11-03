// [统一导航表] 维护入口、文案键和图标键，不导入业务页面，避免共享层反向依赖业务层。
export const navigationPages = [
  { id: 'overview', href: '/', label: 'Overview', icon: 'overview' },
  { id: 'products', href: '/products', label: 'Products', icon: 'products' },
  { id: 'inventory', href: '/inventory', label: 'Inventory', icon: 'inventory' },
  { id: 'orders', href: '/orders', label: 'Orders', icon: 'orders' },
  { id: 'customers', href: '/customers', label: 'Customers', icon: 'customers' },
  { id: 'shipping', href: '/shipping', label: 'Shipping', icon: 'shipping' },
  { id: 'copilot', href: '/inventory?tab=copilot', label: 'Operations advice', icon: 'copilot' },
  { id: 'agent', href: '/agent', label: 'Assistant navigation', icon: 'agent' },
  { id: 'knowledge', href: '/knowledge', label: 'Knowledge base', icon: 'knowledge' },
  {
    id: 'observability',
    href: '/ai-observability',
    label: 'AI observability',
    icon: 'observability',
  },
  { id: 'notifications', href: '/notifications', label: 'Notifications', icon: 'notifications' },
  { id: 'favorites', href: '/favorites', label: 'Favorites', icon: 'favorites' },
] as const
// 从入口表推导高亮类型，新增页面时让侧栏和公共布局保持一致。
export type NavigationPage = (typeof navigationPages)[number]['id']
