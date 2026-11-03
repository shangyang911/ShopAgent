# 前端模块、路由与 Axios 使用说明

## 文件放在哪里

```text
frontend/src/
├─ main.ts                    # 挂载入口、全局样式和地图预加载
├─ App.vue                    # 登录态恢复、登录/注册/错误状态和页面容器
├─ router/
│  ├─ routes.ts               # 路径、名称、页面组件注册表
│  └─ index.ts                # useAppRouter：History 导航及监听清理
├─ app/layouts/               # 需要业务能力的页面布局，如带退出登录的外壳
├─ views/                     # 页面交互与模板
├─ features/
│  ├─ auth/                   # 管理员登录与会话
│  ├─ products/               # 商品
│  ├─ customers/              # 客户和地址
│  ├─ orders/                 # 订单和配货打印
│  ├─ inventory/              # 库存业务类型
│  ├─ dashboard/              # 经营概览业务类型
│  ├─ shipping/               # 运单、包裹、面单、运费、物流和地图
│  └─ agent/                  # 客服会话、录音和播报
├─ shared/
│  ├─ api/                    # Axios、错误解析、下载、查询参数和分页类型
│  ├─ components/             # AppSidebar、AppPagination、语言切换
│  ├─ i18n/                   # 通用语言状态和翻译
│  └─ commerce.ts             # 金额与日期工具
└─ styles/                    # 全局样式
```

功能模块内按需要建立 `api/`、`model/`、`components/`、`composables/` 和 `config/`，不为没有内容的分类创建空文件。`model/` 维护业务类型，`api/` 维护请求路径和参数。公共代码不依赖具体功能模块；需要认证等业务能力的布局放在 `app/`。

## 路由在哪里

路由注册表在 [routes.ts](../frontend/src/router/routes.ts)，导航逻辑在 [router/index.ts](../frontend/src/router/index.ts)。项目沿用浏览器 History 导航，没有安装 Vue Router。

当前管理页面为 `/`（经营概览）、`/products`、`/inventory`、`/orders`、`/customers`、`/shipping`、`/agent`。登录和首次注册页面由 `App.vue` 根据会话恢复结果选择。

站内普通链接会更新 History 并切换当前组件；前进、后退通过 `popstate` 恢复页面。查询参数交给对应页面管理，例如 `/orders?order_id=7`。外链、新标签、下载、锚点和带 Ctrl/Command 等修饰键的点击交给浏览器处理。

新增页面时：

1. 创建 `views/YourView.vue` 和所需功能模块。
2. 在 `router/routes.ts` 中导入组件并增加 `{ path, name, component }`。
3. 在公共侧栏添加入口及对应高亮状态。

`App.vue` 的 KeepAlive 继续保留 `ShippingView`，导航后无需重建地图。部署时需将前端页面路径回退到 `index.html`；API 和静态资源走各自服务。

## Axios 封装在哪里

- [config.ts](../frontend/src/shared/api/config.ts)：读取 `VITE_API_BASE_URL`，默认 `http://当前主机名:8001/api`。
- [httpClient.ts](../frontend/src/shared/api/httpClient.ts)：创建 Axios 实例、携带 Cookie、解析错误，提供 `request<T>()`。
- [errors.ts](../frontend/src/shared/api/errors.ts)：解析 FastAPI 的字符串或 422 校验数组。
- [download.ts](../frontend/src/shared/api/download.ts)：CSV Blob 下载和资源释放。
- [query.ts](../frontend/src/shared/api/query.ts)：URL 查询参数工具。

所有 HTTP 请求使用同一个 Axios 实例，客服实时音频继续走 WebSocket。原先分散的 fetch 请求和运营模块的第二套通用请求封装已移除。

## 怎么发请求

优先在功能模块的 API 文件中使用 `request<T>()`，页面调用对应业务函数。例如：

```ts
// features/orders/api/ordersApi.ts
import { request } from '../../../shared/api/httpClient'
import type { OrderRecord } from '../model/types'

export const fetchOrder = (id: number) =>
  request<OrderRecord>(`/orders/${id}`, 'GET', undefined, {
    fallback: '订单详情加载失败',
  })
```

```ts
// 页面或组合函数
import { fetchOrder } from '../features/orders/api/ordersApi'

try {
  const order = await fetchOrder(7)
  // order 就是后端响应数据，不需要再取 .data。
} catch (error) {
  // 将 error.message 展示在页面已有的错误区域。
}
```

`request<T>(path, method, body, options)` 的常见用法：

```ts
// GET 参数
request('/shipping/workbench', 'GET', undefined, {
  params: { status: 'paid_pending_shipment', page: 1, page_size: 20 },
})

// JSON 提交；Axios 自动序列化 body。
request('/orders/7/status', 'PATCH', {
  status: 'cancelled',
  reason: '客户申请取消',
  idempotency_key: '本次业务操作的固定重试键',
})

// 自定义请求头
request('/orders', 'POST', orderPayload, {
  headers: { 'Idempotency-Key': creationKey },
})

// 图片上传：使用 FormData，不手动设置 multipart Content-Type/boundary。
request('/products/7', 'PUT', formData)

// 获取配货 HTML；普通 request 默认处理 JSON。
request<string>('/order-operations/print-jobs/7/html', 'GET', undefined, {
  responseType: 'text',
})
```

实际修改订单或创建运单时，应复用业务模块中带校验、幂等键和版本号的已有函数。请求层不会自动重试写操作。

需要 Axios 原始响应时，也可导入 `httpClient` 并调用 `httpClient.get<T>()` 或 `httpClient.request<T>()`；这种写法返回 AxiosResponse，需自行取 `.data` 并处理错误。客服模块保留这种方式以保留专用状态码和超时处理。

## 错误与登录态

`request()` 抛出 `ApiError`，其中 `message` 为可展示错误，`status` 为可用的 HTTP 状态码。下载失败返回的 JSON Blob 也会按同样规则解析。

`/auth/me` 返回 401 时，认证模块将其转换为 `null`，供启动时显示登录页；其他错误由调用页面处理。请求层不强制刷新页面或自动跳转，以保留用户正在填写的内容。

## 清理与检查

本次移除了未引用的 `public/icons.svg`、旧配送队列查询/状态修改函数及其专用类型，并将 `features/operations/api.ts` 中的公共工具和各业务类型拆回对应目录。商品页更名为 `ProductsView.vue`；侧栏和分页更名为共享的 `AppSidebar.vue`、`AppPagination.vue`。

不要按文件大小删除功能文件。音频 Worklet 由运行时地址加载；`amap.d.ts` 属于全局类型声明，均需保留。

在 `frontend/` 执行：

```powershell
npm run check:architecture
npm test
npm run build
```

架构检查验证静态导入存在、源码可从 `main.ts` 到达、共享层不反向依赖业务模块；全局 `.d.ts` 声明除外。动态加载资源需结合运行时引用检查。构建还启用了未使用局部变量及参数检查。
