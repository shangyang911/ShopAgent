import { computed, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { routes } from './routes'

// 沿用 History 导航，集中处理站内链接和前进/后退，保留页面自己的查询参数。
export function useAppRouter(authenticated: Readonly<Ref<boolean>>) {
  const pathname = ref(window.location.pathname)
  const locationKey = ref(window.location.pathname + window.location.search)
  const currentRoute = computed(
    () => routes.find((route) => route.path === pathname.value) ?? routes[0],
  )
  // 同一路径下的商品/订单编号也是页面身份；地图页保留固定键以复用 KeepAlive。
  const routeKey = computed(() =>
    currentRoute.value.name === 'shipping' ? '/shipping' : locationKey.value,
  )

  function syncRoute() {
    if (authenticated.value) {
      pathname.value = window.location.pathname
      locationKey.value = window.location.pathname + window.location.search
    }
  }

  function navigateWithinApp(event: MouseEvent) {
    if (
      !authenticated.value ||
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return
    const link =
      event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null
    if (
      !link ||
      link.hasAttribute('download') ||
      (link.target && link.target !== '_self') ||
      link.getAttribute('href')?.startsWith('#')
    )
      return
    const url = new URL(link.href, window.location.href)
    if (
      url.origin !== window.location.origin ||
      !routes.some((route) => route.path === url.pathname)
    )
      return
    event.preventDefault()
    const changed = url.href !== window.location.href
    const sameShippingPage =
      url.pathname === '/shipping' && window.location.pathname === '/shipping'
    if (changed) window.history.pushState(null, '', url.href)
    syncRoute()
    // pushState 不触发 popstate；缓存地图仍需要收到同页链接中的新订单/筛选参数。
    if (changed && sameShippingPage) window.dispatchEvent(new Event('popstate'))
  }

  onMounted(() => {
    document.addEventListener('click', navigateWithinApp)
    window.addEventListener('popstate', syncRoute)
  })
  onBeforeUnmount(() => {
    document.removeEventListener('click', navigateWithinApp)
    window.removeEventListener('popstate', syncRoute)
  })
  return { currentRoute, routeKey, syncRoute }
}
