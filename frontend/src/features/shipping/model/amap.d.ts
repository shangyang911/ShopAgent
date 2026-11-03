// [配送功能 / 高德类型扩展] 声明 Loader 使用前设置的 Nginx 安全代理地址。
export {}

declare global {
  interface Window {
    _AMapSecurityConfig?: {
      serviceHost?: string
    }
  }
}
