<script setup lang="ts">
// [配送功能 / 地图组件] 加载高德地图，并根据父级选择的订单路线绘制起终点和路径。
import { nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useI18n } from '../../../shared/i18n'
import { fetchShippingMapSettings } from '../api/shippingApi'
import type { DeliveryRoute, ShippingMapSettings } from '../model/shipping'
import { getAmapConfig } from '../config/amapConfig'
import { loadAmapApi } from '../config/loadAmapApi'

type Coordinate = [number, number]

interface MapInstance {
  setZoomAndCenter: (zoom: number, center: Coordinate, immediately: boolean) => void
  destroy: () => void
  clearMap: () => void
  add: (overlays: object[]) => void
  setFitView: (
    overlays?: object[],
    immediately?: boolean,
    avoid?: number[],
    maxZoom?: number,
  ) => void
}

interface AMapModule {
  Map: new (element: HTMLElement, options: Record<string, unknown>) => MapInstance
  Marker: new (options: {
    position: Coordinate
    title: string
    anchor: string
    zIndex: number
    label: { content: string; direction: string }
  }) => MarkerInstance
  InfoWindow: new (options: { content: HTMLElement; autoMove: boolean }) => InfoWindowInstance
  Polyline: new (options: {
    path: Coordinate[]
    strokeColor: string
    strokeWeight: number
    strokeOpacity: number
    lineJoin: string
    lineCap: string
  }) => object
}

interface MarkerInstance {
  on: (event: 'click', handler: () => void) => void
}

interface InfoWindowInstance {
  open: (map: MapInstance, position: Coordinate) => void
  close: () => void
}

const props = defineProps<{ route?: DeliveryRoute | null }>()
const { t } = useI18n()
const mapElement = ref<HTMLDivElement | null>(null)
const loading = ref(true)
const mapReady = ref(false)
const errorMessage = ref('')
let map: MapInstance | null = null
let amap: AMapModule | null = null
let activeInfoWindow: InfoWindowInstance | null = null
let active = true
let destroyed = false
let initialSettings: ShippingMapSettings = { center: [104.1954, 35.8617], zoom: 5 }
let settingsPending = false

function applySettings() {
  if (!map || !active || !settingsPending) return
  settingsPending = false
  // 延迟返回的门店视野不能覆盖已经选中的订单路线。
  if (!props.route) map.setZoomAndCenter(initialSettings.zoom, initialSettings.center, true)
}

function renderRoute(route: DeliveryRoute | null | undefined) {
  if (!map || !amap) return
  activeInfoWindow?.close()
  activeInfoWindow = null
  map.clearMap()
  if (!route) return

  // [配送地图 / 路线图层] 路线坐标使用高德经纬度顺序 [lng, lat]。
  const createMarker = (position: Coordinate, title: string, shortLabel: string) => {
    const marker = new amap!.Marker({
      position,
      title,
      anchor: 'bottom',
      zIndex: 120,
      label: {
        content: `<span style="display:inline-block;padding:4px 9px;border-radius:12px;background:#fff;color:#202c31;font:600 12px/1.2 sans-serif;box-shadow:0 2px 8px #202c3133;white-space:nowrap">${shortLabel}</span>`,
        direction: 'top',
      },
    })
    marker.on('click', () => {
      const content = document.createElement('div')
      content.style.cssText =
        'min-width:132px;padding:4px 2px;color:#202c31;font:13px/1.6 sans-serif'
      const heading = document.createElement('strong')
      heading.textContent = title
      const coordinates = document.createElement('div')
      coordinates.style.whiteSpace = 'pre-line'
      coordinates.textContent = `${t('Longitude')}: ${position[0].toFixed(6)}\n${t('Latitude')}: ${position[1].toFixed(6)}`
      content.append(heading, coordinates)
      // [配送地图 / 标记详情] 点击起终点标记后显示对应位置和坐标。
      activeInfoWindow?.close()
      activeInfoWindow = new amap!.InfoWindow({ content, autoMove: true })
      activeInfoWindow.open(map!, position)
    })
    return marker
  }
  const overlays = [
    new amap.Polyline({
      path: route.polyline,
      strokeColor: '#159f91',
      strokeWeight: 6,
      strokeOpacity: 0.9,
      lineJoin: 'round',
      lineCap: 'round',
    }),
    createMarker(route.origin, t('Dispatch origin'), t('Start point')),
    createMarker(route.destination, t('Delivery destination'), t('End point')),
  ]
  map.add(overlays)
  map.setFitView(overlays, true, [48, 48, 48, 48], 16)
}

watch(() => props.route, renderRoute)

function createMap() {
  if (destroyed || !active || map || !amap || !initialSettings) return
  if (!mapElement.value) {
    loading.value = false
    errorMessage.value = t('Map container unavailable')
    return
  }
  try {
    errorMessage.value = ''
    map = new amap.Map(mapElement.value, {
      viewMode: '2D',
      zoom: initialSettings.zoom,
      center: initialSettings.center,
      resizeEnable: true,
      animateEnable: false,
    })
    mapReady.value = true
    settingsPending = false
    renderRoute(props.route)
  } catch (error) {
    console.error('[配送功能 / 高德地图] 初始化失败', error)
    errorMessage.value = t('Amap failed to load')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  // [配送地图 / 安全代理] serviceHost 必须在 Loader 执行前写入，安全密钥由 Nginx 注入。
  const { key, serviceHost } = getAmapConfig()
  if (!key || !serviceHost) {
    loading.value = false
    errorMessage.value = t('Amap credentials missing')
    return
  }
  try {
    // 门店配置只影响初始视野，慢请求或失败不应阻塞底图。
    void fetchShippingMapSettings()
      .then((settings) => {
        if (destroyed) return
        initialSettings = settings
        settingsPending = true
        applySettings()
      })
      .catch((error: unknown) => {
        if (!destroyed) console.warn('[配送地图] 门店视野加载失败，使用默认视野', error)
      })
    const module = await loadAmapApi()
    if (destroyed) return
    amap = module as AMapModule
    createMap()
  } catch (error) {
    if (destroyed) return
    console.error('[配送功能 / 高德地图] 加载失败', error)
    errorMessage.value = t('Amap failed to load')
    loading.value = false
  }
})

onActivated(async () => {
  active = true
  await nextTick()
  createMap()
  applySettings()
})
onDeactivated(() => {
  active = false
  activeInfoWindow?.close()
})
onBeforeUnmount(() => {
  // 缓存页面切换时保留地图；应用实际卸载时才释放实例和监听器。
  destroyed = true
  activeInfoWindow?.close()
  map?.destroy()
  activeInfoWindow = null
  map = null
  amap = null
})
</script>

<template>
  <!-- [配送功能 / 地图容器] 地图目标节点由 ref 传给 AMap.Map，避免重复 DOM id。 -->
  <section class="shipping-map-shell" :aria-label="t('Delivery map')">
    <div ref="mapElement" class="shipping-map-canvas"></div>
    <div v-if="loading" class="shipping-map-overlay" role="status">{{ t('Loading map…') }}</div>
    <div v-else-if="errorMessage" class="shipping-map-overlay shipping-map-notice" role="status">
      <strong>{{ mapReady ? t('Map loaded') : t('Map setup needed') }}</strong>
      <p>{{ errorMessage }}</p>
      <code v-if="!mapReady">VITE_AMAP_KEY<br />VITE_AMAP_SERVICE_HOST</code>
    </div>
  </section>
</template>
