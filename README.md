# ShopAgent · 单店电商管理与 AI 运营助手

ShopAgent 面向单店管理员，将商品、客户、订单、库存、售后和发货物流集中在一个后台，并提供文字/语音运营助手、门店知识库及 AI 运行记录。

本文件是项目整体大纲与开发入口。功能说明以当前仓库源码为准，详细接口、配置和业务规则见各模块文档。

重新配置 GitHub Actions 工作流与仓库密钥后，可启用 `master` 分支的自动测试、备份和部署。服务器以 `/opt/shopagent/releases/<提交 SHA>` 保存版本，`/opt/shopagent/current` 指向当前线上版本，详细操作见 [部署说明](deploy/README.md)。

## 目录

- [一、项目定位](#一项目定位)
- [二、功能模块与页面入口](#二功能模块与页面入口)
- [三、核心业务流程](#三核心业务流程)
- [四、技术组成与代码组织](#四技术组成与代码组织)
- [五、本地启动](#五本地启动)
- [六、外部服务配置](#六外部服务配置)
- [七、测试与构建](#七测试与构建)
- [八、数据与配置管理](#八数据与配置管理)
- [九、详细文档索引](#九详细文档索引)

## 一、项目定位

- **使用方式**：单店、单管理员后台。首次注册创建管理员，之后关闭注册，通过登录会话访问管理功能。
- **业务目标**：串联商品维护、客户建档、开单收款、库存预留、发货交接、物流查询和售后处理。
- **AI 定位**：辅助查询商品、订单与物流，检索已发布的门店知识，提供补货和待发货分析；涉及业务写入时遵循对应操作的确认流程。
- **页面结构**：首页展示经营概览，业务页面按商品、库存、订单、客户、发货和运营助手划分。

## 二、功能模块与页面入口

### 1. 经营概览与日常提醒

- **经营概览 `/`**：查看指定时间范围的经营指标、趋势、库存提醒和待发货入口。
- **消息中心 `/notifications`**：集中查看原有库存、发货提醒，并跳转对应业务页面。

### 2. 商品与库存

- **商品管理 `/products`**：商品维护、图片上传、搜索、组合筛选、排序、分页及批量操作。
- **商品详情 `/products/detail?id=...`**：多图展示、SKU 规格、价格和库存联动，支持带选定规格进入开单。
- **商品收藏 `/favorites`**：查看和管理收藏商品。
- **库存中心 `/inventory`**：查看在库、预留与可售数量，管理入库、流水、盘点、报损和纠错，并提供补货建议。

### 3. 客户、订单与售后

- **客户管理 `/customers`**：维护客户资料、地址簿、历史订单和跟进记录。
- **订单工作台 `/orders`**：创建订单、筛选查询、确认收款、取消订单、查看操作时间线、导出 CSV 和打印配货单。
- **订单详情内的售后面板**：登记售后申请、实际退款和退货验收，按业务规则处理订单及库存。

### 4. 发货与物流

入口为 **`/shipping`**，包含以下能力：

- **运单与交接**：登记快递公司和运单号，确认实物交接，支持历史运单补录。
- **电子面单与批量操作**：配置快递账号后申请、核实和打印面单，支持批量取号及批量交接。
- **包裹与运费**：一单多包裹、包裹独立运单、实际运费登记、汇总和导出。
- **物流轨迹**：通过快递100查询并缓存各包裹轨迹，展示同步与签收状态。
- **高德地图**：展示发货点、收货地址及驾车路线参考；路线距离和耗时不代表快递运输轨迹或送达承诺。

### 5. AI 运营与知识管理

- **运营助手 `/agent`**：文字/语音对话、业务工具查询、确认后取消订单、人工工单及历史会话恢复。
- **门店知识库 `/knowledge`**：导入文本或 Markdown，管理草稿、发布、有效期与撤回，查看回答引用的版本原文。
- **AI 运行面板 `/ai-observability`**：查看请求轨迹、工具调用、耗时、Token 用量、费用估算和用户反馈。
- **运营 Copilot**：在对应业务页面提供智能补货、待发货分析及核对后创建入库草稿的能力。

知识库当前采用本地字面混合检索，支持版本化引用；可按配置选择云端知识源。费用指标是配置单价下的估算，实际覆盖范围见 [AI 运行面板说明](docs/AI-OBSERVABILITY.md)。

## 三、核心业务流程

### 1. 正常履约

```text
维护商品与 SKU → 建立客户和收货地址 → 创建订单并预留库存
→ 确认收款 → 登记运单或申请电子面单 → 确认实物交接并出库
→ 查询物流 → 确认完成
```

订单主状态依次为：

```text
pending_payment → paid_pending_shipment → in_delivery → completed
```

取消与退款按订单状态进入相应分支，具体规则见 [核心模块说明](docs/IMPLEMENTATION-core-modules.md) 和 [售后与库存纠错说明](docs/PLATFORM-OPTIMIZATION.md)。

### 2. 库存、金额与历史记录

- 创建订单时预留库存；取消尚未出库的订单时按规则释放预留。
- 保存运单、取得面单和打开打印页均不代表完成出库；确认实物交接时才更新库存及流水。
- 一单多包裹按整单交接，避免重复出库。
- 订单冻结商品价格和收货地址快照，后续修改商品或客户地址不覆盖原订单快照。
- 实际物流支出与向客户收取的运费分别记录；退款登记、退货验收和库存恢复遵循各自流程。

### 3. AI 查询与操作

```text
管理员提问 → 查询业务工具或检索有效知识 → 返回结果与相关依据
→ 需要业务操作时展示待确认动作 → 管理员确认 → 执行并记录结果
```

知识需由管理员核对后发布；缺少有效政策时不将演示资料当作真实门店承诺。引用和离线评测的适用边界见 [知识库说明](docs/KNOWLEDGE-EVIDENCE.md) 与 [Agent 评测说明](docs/AGENT-EVALUATION.md)。

## 四、技术组成与代码组织

### 技术组成

- **前端**：Vue 3、TypeScript、Vite、Axios；使用项目内的轻量 History 导航。
- **后端**：FastAPI、Uvicorn、SQLAlchemy、PyMySQL；API 统一使用 `/api` 前缀。
- **持久化**：MySQL 保存业务数据、会话及运行记录；商品图片保存在 `backend/uploads/`。
- **实时交互**：WebSocket 支持运营助手的文字、语音和事件交互。
- **外部能力**：高德地图、快递100及阿里云百炼，按功能分别配置。

### 目录大纲

```text
ShopAgent/
├─ README.md                    # 项目总览、启动入口和文档导航
├─ frontend/
│  ├─ src/
│  │  ├─ App.vue                # 应用入口、登录态和页面容器
│  │  ├─ router/                # 页面注册与 History 导航
│  │  ├─ views/                 # 业务页面组合
│  │  ├─ features/              # 各业务模块的组件、接口、类型和状态
│  │  ├─ shared/                # 请求封装、公共组件、导航和国际化
│  │  └─ styles/                # 全局样式
│  ├─ public/                   # 静态资源和录音 Worklet
│  ├─ scripts/                  # 前端架构检查
│  └─ tests/                    # 前端回归测试
├─ backend/
│  ├─ app/
│  │  ├─ main.py               # FastAPI 入口与生命周期
│  │  ├─ api/router.py         # /api 路由汇总
│  │  ├─ core/                 # 环境配置
│  │  ├─ db/                   # 数据库连接、模型基类和初始化
│  │  └─ features/             # 认证、商品、客户、订单、库存、物流及 AI
│  ├─ evals/                    # Agent 与知识检索评测
│  ├─ scripts/                  # 数据维护与开发辅助脚本
│  ├─ tests/                    # 后端业务回归测试
│  ├─ deploy/                   # 高德代理等部署配置模板
│  ├─ uploads/                  # 本地上传文件，不提交 Git
│  └─ start-dev.ps1             # API 与本地地图代理启动脚本
└─ docs/                        # 业务规则、接口协议与模块说明
```

### 修改入口

- **新增或调整页面**：从 `frontend/src/views/` 和 `frontend/src/router/routes.ts` 开始，业务实现放入对应 `features/` 模块。
- **调整业务接口**：从 `backend/app/api/router.py` 找到对应模块，业务规则和持久化实现集中在 `backend/app/features/`。
- **修改公共请求或语言显示**：查看 `frontend/src/shared/`；接口字段与筛选值保持语言无关。
- **修改数据库连接或外部服务参数**：编辑本地环境配置，参考前后端 `.env.example` 和详细 README。

## 五、本地启动

以下命令使用 Windows PowerShell。需要准备 Python、Node.js/npm 和可连接的 MySQL，依赖以仓库清单及锁文件为准。

### 1. 获取项目

```powershell
git clone git@gitee.com:up-above/shop-agent.git
cd shop-agent
```

已有本地项目时直接进入项目根目录，无需重新克隆。

### 2. 配置并启动后端

在项目根目录打开终端：

```powershell
cd backend
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `backend/.env`，将 `DATABASE_URL` 改为实际 MySQL 连接地址，并确保目标数据库已创建。然后启动 API：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```

- API 文档：[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)
- 健康检查：[http://127.0.0.1:8001/api/health](http://127.0.0.1:8001/api/health)

当前版本启动时会执行表结构兼容、商品种子及运营期初初始化。首次使用请连接专用开发数据库，详细行为见 [后端说明](backend/README.md#数据关系与历史初始化)。

### 3. 配置并启动前端

另开终端，从项目根目录执行：

```powershell
cd frontend
npm ci
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
npm run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。首次进入按页面提示创建管理员，之后使用该账号登录。

前端默认连接 `http://当前页面主机名:8001/api`。如需修改，在 `frontend/.env.local` 中设置 `VITE_API_BASE_URL`。前后端统一使用 `127.0.0.1` 或 `localhost`，保持登录 Cookie 的主机名一致。

### 4. 需要地图代理时

先按 [高德代理配置](backend/README.md#高德地图与代理) 配置本地代理文件，再用以下命令替代单独启动 API 的命令：

```powershell
# 在 backend/ 目录执行，同时启动 API（8001）和地图代理（8080）
.\start-dev.ps1
```

## 六、外部服务配置

基础业务与外部服务分开配置，凭证及适用条件见 [后端环境配置](backend/README.md#环境配置)。

- **MySQL**：`DATABASE_URL` 为后端必填配置。
- **高德地图**：前端配置 `VITE_AMAP_KEY`、`VITE_AMAP_SERVICE_HOST`；后端配置 Web 服务 Key、代理及真实发货点坐标。安全密钥仅保存在代理端。
- **快递100物流查询**：配置 `KD100_CUSTOMER`、`KD100_KEY`；后台自动同步需另外启用，默认关闭。
- **快递100电子面单**：独立配置 `KD100_LABEL_*` 授权、寄件资料和快递账号，不能用物流查询凭证代替。
- **百炼运营助手**：配置 `DASHSCOPE_API_KEY` 及对话、语音相关模型参数；本地门店知识库不依赖云端知识库服务。

未配置外部服务时，对应地图、查询、取号或模型能力不可用；不能据此认定已经完成真实地图、快递平台或模型联调。

## 七、测试与构建

### 前端

在 `frontend/` 目录执行：

```powershell
npm test
npm run check:architecture
npm run build
```

分别检查回归行为、源码依赖边界，以及类型检查和生产构建。

### 后端与 Agent

在 `backend/` 目录执行：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m evals.runner --mode offline
.venv\Scripts\python.exe -m evals.rag
```

默认回归测试和上述离线评测使用隔离数据或测试夹具。MySQL 并发测试与真实模型评测需单独配置，执行方式分别见 [后端测试说明](backend/README.md#测试与测试数据) 和 [Agent 评测说明](docs/AGENT-EVALUATION.md)。离线通过不代表真实供应商能力已验收。

## 八、数据与配置管理

- `.env`、前端本地配置、`frontend/dist/`、依赖目录、上传文件、IDE 私有配置和测试备份不提交 Git。
- `VITE_` 变量会进入浏览器代码，不能保存后端密钥；高德 JS API Key 应限制允许访问的域名。
- MySQL 数据、上传图片和本地配置独立于 Git。克隆、拉取或回退代码不会自动同步现有数据库，也不会把已有真实客户资料替换为演示资料。
- 演示客户使用脱敏手机号和标明虚构的地址，仅供本地展示，不用于真实投递、面单申请或精确路线验收。
- 数据维护脚本应先核对目标数据库与执行说明，具体操作见后端文档。

## 九、详细文档索引

### 开发与核心业务

- [前端说明](frontend/README.md)：启动、页面、环境配置和开发约定。
- [后端说明](backend/README.md)：数据库、API、服务配置、初始化与测试。
- [前端模块与请求开发说明](docs/FRONTEND-STRUCTURE.md)：路由、Axios 和模块边界。
- [核心模块实现说明](docs/IMPLEMENTATION-core-modules.md)：经营概览、库存与订单的实际实现。
- [核心模块需求稿](docs/PRD-core-modules-v1.md)：需求背景及验收口径，实际能力以源码和实现说明为准。
- [商品详情与 SKU](docs/PRODUCT-DETAILS.md)：规格、价格和库存联动。
- [运营功能说明](docs/OPERATIONS-ENHANCEMENTS.md)：消息、商品批量操作和客户详情。
- [售后、库存纠错与包裹物流](docs/PLATFORM-OPTIMIZATION.md)：跨模块业务规则。

### 发货与物流

- [发货一期](docs/SHIPPING-phase1.md)：运单登记、实物交接、库存行为和历史补录。
- [物流二期](docs/SHIPPING-phase2.md)：快递100查询、缓存与同步配置。
- [面单与运费三期](docs/SHIPPING-phase3.md)：电子面单、批量发货、多包裹和实际运费。

### AI、知识库与评测

- [Agent 接口协议](docs/AGENT-API.md)：文字、语音、业务确认及人工工单。
- [运营 Copilot](docs/OPERATIONS-COPILOT.md)：补货建议和待发货分析。
- [门店知识库与引用证据](docs/KNOWLEDGE-EVIDENCE.md)：知识版本、生效规则和检索边界。
- [AI 运行面板](docs/AI-OBSERVABILITY.md)：轨迹、工具、费用估算和反馈。
- [Agent 评测体系](docs/AGENT-EVALUATION.md)：固定任务集、离线评测与真实模型评测。
