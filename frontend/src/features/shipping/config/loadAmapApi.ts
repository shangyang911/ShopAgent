import AMapLoader from '@amap/amap-jsapi-loader'
import { getAmapConfig } from './amapConfig'

// Loader 会复用已加载的 SDK 和进行中的请求，刷新配送页时也可提前开始下载。
export function loadAmapApi() {
  const { key, serviceHost } = getAmapConfig()
  if (!key || !serviceHost) return Promise.reject(new Error('Amap credentials missing'))
  window._AMapSecurityConfig = { serviceHost }
  return AMapLoader.load({ key, version: '2.0' })
}
