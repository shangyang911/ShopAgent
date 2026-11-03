# 前端说明

基于 Vue 3、TypeScript 和 Vite 的单店电商管理界面，提供经营概览、商品、库存、订单、客户、发货物流及智能客服页面。

- [快速启动](#快速启动)
- [环境配置](#环境配置)
- [页面与业务功能](#页面与业务功能)
- [目录结构与开发约定](#目录结构与开发约定)
- [路由与 Axios 请求](#路由与-axios-请求)
- [测试与构建](#测试与构建)
- [相关文档](#相关文档)

## 快速启动

需要安装 Node.js 和 npm；依赖版本见 [package.json](package.json)。从项目根目录进入前端目录：

```powershell
cd frontend
npm install
```

首次配置时，将 [.env.example](.env.example) 复制为 `.env.local`；已有配置则直接编辑，保留现有值。随后启动：

```powershell
npm run dev
```

默认访问 [前端页面](http://127.0.0.1:5173/)。后端启动步骤见 [后端 README](../backend/README.md)。

首次使用时访问 `/register` 创建唯一管理员；创建后注册关闭，后续从 `/login` 登录。接口使用 HttpOnly Cookie 恢复会话。

本文件后续命令均在 `frontend/` 目录执行。

## 环境配置

### 后端地址与登录态

- `VITE_API_BASE_URL`：可选，覆盖完整 API 地址，包含 `/api` 前缀。
- 未设置时，默认使用 `http://当前页面主机名:8001/api`。
- 本地访问时，前后端统一使用 `localhost` 或 `127.0.0.1`，保持 Cookie 主机一致。
- 修改环境变量后需重启开发服务器；生产构建时使用构建环境中的变量。

如需指定地址，可在 `.env.local` 中填写：

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8001/api
```

### 高德地图

在 `.env.local` 中配置：

```dotenv
VITE_AMAP_KEY=你的高德JS_API_Key
VITE_AMAP_SERVICE_HOST=http://127.0.0.1:8080/_AMapService
```

`VITE_AMAP_KEY` 会出现在浏览器中，应在高德控制台限制允许访问的域名。安全密钥 `jscode` 只配置在代理端，不放入任何 `VITE_` 变量。

地图通过 `serviceHost` 访问代理；后端 Web 服务 Key、发货点坐标及代理启动方式见 [后端地图配置](../backend/README.md#高德地图与代理)。地图不可用时仍可登记运单和确认快递交接。

### 外部服务凭证

快递100物流查询、电子面单及百炼智能客服的凭证均由后端配置，前端只读取服务可用状态。具体配置见 [后端环境配置](../backend/README.md#环境配置)。

## 页面与业务功能

本轮新增订单详情售后面板、库存盘点纠错面板和包裹级物流，并调整商品经营状态与运营助手定位。操作规则见 [平台业务闭环优化](../docs/PLATFORM-OPTIMIZATION.md)。

### 经营概览、商品和库存

- `/`：经营概览，查看业务指标、发货待办及库存提醒。
- `/products`：商品搜索、分类/价格/颜色/评分/配送方式/状态筛选、排序、分页及商品维护。
- `/products/detail?id=...`：多图轮播、SKU 选择、库存联动和图文/参数 Tab；可管理图片与规格，并带 SKU 进入开单。接口与库存口径见 [商品详情说明](../docs/PRODUCT-DETAILS.md)。
- `/inventory`：库存查询、入库、预留与库存流水管理。

业务口径见 [核心模块实现说明](../docs/IMPLEMENTATION-core-modules.md)。

### 订单和客户

- `/orders`：订单列表、筛选、详情、状态操作、时间线、CSV 导出、配货单打印及合并配货汇总。
- `/customers`：客户和地址簿管理，为下单提供客户与收货地址。
- 订单筛选方案按账号保存在浏览器中；配货打印基于冻结单据，并在打开打印前核对当前订单状态。

### 发货与物流

入口为 `/shipping`，按履约流程组织功能：

- **运单登记与交接**：待发货、已发货、已完成列表；登记快递公司、运单号和备注；历史信息补录；与订单页双向跳转。保存运单不改库存，实物交接后才确认出库。
- **电子面单与批量发货**：单笔或批量取号、面单打印、批量交接、逐单失败明细及异常取号结果核实。未配置账号时明确显示不可用状态。
- **多包裹与实际运费**：按需增加包裹，分别记录运单和结算运费；支持汇总、日期/快递筛选及 CSV 导出。整单全部包裹统一交接，订单只出库一次。
- **物流轨迹**：轨迹时间线和同步状态，各包裹独立查询并汇总签收状态。物流异常待办及人工跟进已移除。
- **高德地图与路线参考**：查看发货点至收货地址的驾车路线；距离和耗时不代表快递运输轨迹或送达承诺。

操作规则和配置分别见 [发货基础流程](../docs/SHIPPING-phase1.md)、[物流查询](../docs/SHIPPING-phase2.md)、[电子面单与运费管理](../docs/SHIPPING-phase3.md)。

### 店内运营助手

入口为 `/agent`，支持文字提问、按住录音/松开发送/上滑取消、流式语音播报与打断、业务动作确认、人工工单和历史会话恢复。

麦克风需要 localhost 或 HTTPS 环境。`public/agent-pcm-worklet.js` 将录音转换为 16 kHz PCM；回复音频以 24 kHz PCM 排队播放。前端复用管理员 Cookie，不配置百炼 Key。协议和服务边界见 [Agent 接口文档](../docs/AGENT-API.md)。

## 目录结构与开发约定

```text
frontend/
├─ src/
│  ├─ App.vue             # 页面选择、登录态恢复和站内导航
│  ├─ router/             # 路由注册表和 History 导航
│  ├─ app/layouts/        # 带认证等业务能力的页面布局
│  ├─ views/              # 页面组合
│  ├─ features/
│  │  ├─ auth/            # 认证接口
│  │  ├─ products/        # 商品接口、类型、状态和组件
│  │  ├─ inventory/       # 库存业务类型
│  │  ├─ dashboard/       # 经营概览业务类型
│  │  ├─ orders/          # 订单接口
│  │  ├─ customers/       # 客户与地址接口
│  │  ├─ shipping/        # 发货、包裹、面单、运费、物流和地图
│  │  └─ agent/           # 客服会话、接口、录音和播放
│  ├─ shared/             # Axios、下载、查询参数、语言和公共组件
│  └─ styles/             # 全局样式
├─ public/                # 静态资源和音频 Worklet
└─ tests/                 # 前端回归测试与测试工具
```

页面负责组合组件；功能模块维护各自的接口、模型和业务状态；共享代码不反向依赖功能模块。商品页的调用顺序为 `ProductsView.vue → useProductsCatalog → productsApi.ts`，组件接收类型化数据并通过事件提交用户操作。

站内导航使用浏览器 History。配送页面通过 KeepAlive 保留地图、视野和路线，返回时刷新订单队列；其他管理页面打开时重新加载数据。地图配置和 SDK 并行加载，SDK 就绪后初始化 2D 地图。

选中的语言保存在浏览器中。接口字段和筛选值保持语言无关，翻译只作用于展示文本。编辑器建议使用 Vue 和 Prettier 扩展。

## 路由与 Axios 请求

路由注册在 [src/router/routes.ts](src/router/routes.ts)，History 链接拦截和前进/后退逻辑在 [src/router/index.ts](src/router/index.ts)。项目使用自己的轻量导航，没有安装 Vue Router；`App.vue` 负责会话恢复和页面容器。

Axios 实例和通用 `request<T>()` 在 [src/shared/api/httpClient.ts](src/shared/api/httpClient.ts)，接口根地址在 [config.ts](src/shared/api/config.ts)。业务 API 统一使用该实例，携带 Cookie，统一处理 FastAPI 错误；`request<T>()` 直接返回响应数据。CSV 下载和查询参数工具分别放在 `shared/api/download.ts`、`shared/api/query.ts`。

新增接口优先写入相应 `features/<模块>/api/` 文件，业务类型写入 `model/`，再由页面调用。上传、JSON、配货 HTML、错误处理及新增路由的具体示例见 [前端模块与请求开发说明](../docs/FRONTEND-STRUCTURE.md)。

## 测试与构建

- `npm run dev`：启动开发服务器。
- `npm test`：运行前端回归测试。
- `npm run check:architecture`：检查导入存在、源码可达性和共享层依赖边界。
- `npm run build`：执行 TypeScript/Vue 类型检查并生成 `dist/` 生产构建。
- `npm run preview`：本地预览生产构建。
- `npm run format`：格式化 Vue、TypeScript、CSS 及项目入口配置。
- `npm run format:check`：检查格式。

回归测试使用内存渲染器和受控接口响应，无需启动后端或浏览器。覆盖请求先后顺序、地址切换、分页、地图生命周期、客服录音与重连、物流跟进、包裹版本、批量重试、运费结算和面单打印加载保护。

## 相关文档

- [项目入口](../README.md)与[后端说明](../backend/README.md)
- [前端模块、路由与 Axios 开发说明](../docs/FRONTEND-STRUCTURE.md)
- [核心模块需求](../docs/PRD-core-modules-v1.md)与[实现说明](../docs/IMPLEMENTATION-core-modules.md)
- [发货基础流程](../docs/SHIPPING-phase1.md)
- [物流查询](../docs/SHIPPING-phase2.md)
- [电子面单、多包裹与实际运费](../docs/SHIPPING-phase3.md)
- [Agent 接口协议](../docs/AGENT-API.md)
