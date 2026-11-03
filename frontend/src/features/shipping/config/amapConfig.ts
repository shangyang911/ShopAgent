// [配送功能 / 高德配置] 读取浏览器端 JS API Key 和 Nginx serviceHost；安全密钥只保存在代理端。
export function getAmapConfig() {
  return {
    key: import.meta.env.VITE_AMAP_KEY?.trim() ?? '',
    serviceHost: import.meta.env.VITE_AMAP_SERVICE_HOST?.trim() ?? '',
  }
}
