// 所有功能共用同一后端地址；使用当前主机名，让 localhost 和 127.0.0.1 各自保持 Cookie 同站。
export const API_BASE =
  import.meta.env.VITE_API_BASE_URL ?? `http://${window.location.hostname}:8001/api`
