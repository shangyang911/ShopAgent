# 后端说明

基于 FastAPI、SQLAlchemy 和 MySQL 的单店电商管理 API，按业务模块组织认证、商品、客户、订单、库存运营、发货物流和智能客服功能。

- [快速启动](#快速启动)
- [环境配置](#环境配置)
- [业务模块与接口](#业务模块与接口)
- [数据与业务约束](#数据与业务约束)
- [目录结构与开发约定](#目录结构与开发约定)
- [测试与测试数据](#测试与测试数据)
- [相关文档](#相关文档)

## 快速启动

需要安装 Python，并准备可连接的 MySQL 数据库。从项目根目录进入后端目录：

```powershell
cd backend
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

首次配置时，将 [.env.example](.env.example) 复制为 `.env`，填写 `DATABASE_URL`；已有配置则直接编辑，保留现有值。接下来根据需要选择启动方式。

### 启动 API 与本地地图代理

```powershell
.\start-dev.ps1
```

脚本在默认 8001 端口启动 API，并启动或复用 8080 端口的本地高德代理；退出时只停止由该脚本启动的代理。代理读取本地 `deploy/nginx/amap-proxy.conf` 中的安全密钥，使用前需配置该文件。修改 API 端口可执行 `.\start-dev.ps1 -Port 8002`。

### 仅启动 API

不需要本地地图代理，或已部署独立代理时，可单独启动：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```

- [API 文档](http://127.0.0.1:8001/docs)
- [健康检查](http://127.0.0.1:8001/api/health)
- [商品列表](http://127.0.0.1:8001/api/products)

前端启动步骤见 [前端 README](../frontend/README.md)。首次访问前端 `/register` 创建唯一管理员，后续通过 `/login` 登录；注册在管理员创建后关闭。

本文件后续命令均在 `backend/` 目录执行。

## 环境配置

配置保存在 `backend/.env`，完整变量示例见 [.env.example](.env.example)。服务端凭证不放入前端 `VITE_` 变量，修改配置后重启后端。

### 数据库与登录 Cookie

- `DATABASE_URL`：必填，例如 `mysql+pymysql://<USER>:<PASSWORD>@<HOST>:3306/<DATABASE>?charset=utf8mb4`。
- `AUTH_COOKIE_SECURE`：本地 HTTP 默认 `false`；通过 HTTPS 提供 API 时设置为 `true`。

认证使用 HttpOnly 会话 Cookie。客户、地址、订单及运营管理接口要求管理员登录。前后端主机名需保持一致；更换前端来源时同步核对 `app/main.py` 中的 CORS 配置。

### 高德地图与代理

- `AMAP_WEB_KEY`：后端高德 Web 服务 Key。
- `AMAP_SERVICE_PROXY_URL`：高德服务代理地址，默认 `http://127.0.0.1:8080/_AMapService`。
- `AMAP_SHIPPING_ORIGIN_LONGITUDE`、`AMAP_SHIPPING_ORIGIN_LATITUDE`：真实门店或仓库的 GCJ-02 坐标，经度在前。

前端另外配置 `VITE_AMAP_KEY` 和 `VITE_AMAP_SERVICE_HOST`，见 [前端地图配置](../frontend/README.md#高德地图)。安全密钥 `jscode` 仅由代理注入，后端不直接发送。

代理可采用以下方式：

- **Nginx**：参考 [配置模板](deploy/nginx/amap-proxy.conf.example)，将实际安全密钥保存在本地忽略的 `deploy/nginx/amap-proxy.conf`。
- **Windows 本地代理**：运行 `.venv\Scripts\python.exe dev_amap_proxy.py`，默认监听 `127.0.0.1:8080`；通常由 `start-dev.ps1` 自动管理。

地址解析和路线规划会发起真实外部请求，需要有效凭证、代理和真实发货点。代理不可用返回 503，订单地址或地图请求不可用返回 422。无坐标或地图错误不阻止快递运单登记和交接出库。

### 快递100物流查询

物流异常待办功能已移除。清理旧环境残留的三张待办表可运行 `scripts/cleanup_retired_tables.py --database dianshang` 先查看检查结果，再加 `--apply` 备份并清理。脚本只处理明确停用的表，备份保存于 `.local-test/database-backups/`，保留表的外键引用会阻止清理。

- `KD100_CUSTOMER`、`KD100_KEY`：物流查询账号和授权。
- `LOGISTICS_AUTO_SYNC`：是否启用后台查询，默认 `false`。
- `LOGISTICS_SYNC_INTERVAL_MINUTES`：同单查询间隔，最低 30 分钟。
- `KD100_CARRIER_CODES`：自定义快递公司名到标准编码的 JSON 映射。

未配置时不查询平台、不生成虚构轨迹，人工发货和地图功能仍可使用。详细规则见 [物流查询](../docs/SHIPPING-phase2.md)。

### 快递100电子面单

电子面单使用独立授权，与物流查询配置分开：

- `KD100_LABEL_KEY`、`KD100_LABEL_SECRET`：企业授权和密钥。
- `KD100_LABEL_SENDER`：寄件人姓名、电话和完整地址的 JSON 对象。
- `KD100_LABEL_ACCOUNTS`：按快递编码配置月结账号、网点参数和面单模板的 JSON 对象。

缺少配置时不向平台取号。真实取号、图片打印、超时核实及平台作废的边界见 [电子面单配置说明](../docs/SHIPPING-phase3.md#电子面单配置)。

### 百炼运营助手

- `DASHSCOPE_API_KEY`、`DASHSCOPE_REGION`：百炼凭证和地域。
- `AGENT_MODEL`、`AGENT_ASR_MODEL`、`AGENT_TTS_MODEL`、`AGENT_TTS_VOICE`：对话、识别、语音合成模型及音色。
- `AGENT_ALLOWED_ORIGINS`：允许建立客服 WebSocket 的前端来源。
- `DASHSCOPE_WORKSPACE_ID`、`BAILIAN_KNOWLEDGE_BASE_ID`：按需要配置业务空间和知识库。
- `DASHSCOPE_BASE_URL`、`DASHSCOPE_WS_URL`、`BAILIAN_RAG_URL`：按需要覆盖服务地址。

没有百炼 Key 时应用仍能启动，相关模型能力不可用；知识库需要另外配置，人工转接目前采用持久化工单。事件协议见 [Agent 接口文档](../docs/AGENT-API.md)。

## 业务模块与接口

消息待办、商品批量管理和客户详情的入口、模块划分与事务约定见[运营功能说明](../docs/OPERATIONS-ENHANCEMENTS.md)。

所有业务接口统一挂载在 `/api`；完整请求和响应以运行中的 API 文档为准。

### 认证、商品和客户

- `/api/auth`：首位管理员注册、登录、会话恢复及退出。
- `/api/products`：商品 CRUD、搜索、筛选、排序、分页和商品图片上传。
- `/api/customers`、`/api/addresses`：客户与地址簿管理；地址包含收件信息、行政区域、详细地址和可选坐标。

商品图片通过 `multipart/form-data` 上传，支持不超过 5 MB 的 JPEG、PNG、WebP。文件保存在 `uploads/products/`，数据库仅保存相对路径；`uploads/` 已忽略版本管理。

### 订单、库存和经营概览

- `/api/orders`：订单创建、列表、详情、状态流转和按条件导出 CSV。
- `/api/order-operations`：订单时间线、带原因的事件更正、配货单打印和冻结单据汇总。
- `/api/inventory`：库存、入库、库存流水和告警。
- `/api/dashboard`：经营概览与相关业务入口。

新订单使用客户、地址、商品 ID 和数量提交。后端核对地址归属及可用库存，根据商品目录计算价格、预留库存，并冻结收件地址和商品信息。业务口径及接口细节见 [核心模块实现说明](../docs/IMPLEMENTATION-core-modules.md)。

### 发货、包裹、面单和运费

- `GET /api/shipping/workbench`：发货工作台列表，支持订单、收件人、手机号及运单搜索，不调用地图服务。
- `PUT /api/shipping/orders/{id}/shipment`：登记一号包裹的快递信息。
- `POST /api/shipping/orders/{id}/dispatch`、`POST /api/shipping/dispatch-batch`：单笔或批量确认交接出库。
- `/api/shipping/orders/{id}/packages`：包裹清单及附加包裹维护；包裹下的 `/label`、`/freight` 用于面单取号和实际运费登记。
- `POST /api/shipping/labels/print-batch`：校验电子面单批次并登记打印意图。
- `/api/shipping/freight-report` 及 `/export`：按交接时间和快递公司汇总、导出实际运费。

历史已发货订单可带来源备注补录主运单，不重复出库。电子面单、多包裹和结算完整接口见 [三期说明](../docs/SHIPPING-phase3.md)。

### 物流查询和地图

物流模块提供快递100查询、缓存轨迹、轨迹去重和可选后台轮询，物流事实独立于订单人工完成状态。主包裹和附加包裹均支持独立查询；查询规则见 [二期说明](../docs/SHIPPING-phase2.md)。物流异常待办、人工跟进接口及后台待办生成已移除。

地图接口保留在 `/api/shipping` 下：

- `GET /orders`：待发货及配送中订单，按需解析地址坐标；失败时保留订单并返回 `geocode_error`。
- `GET /orders/{id}/route`：发货点至收货地址的驾车距离、耗时和路线。
- `PATCH /orders/{id}/status`：保留人工确认完成操作；直接提交 `in_delivery` 会被拒绝，交接必须使用专用接口。

### 店内运营助手

`/api/agent` 复用管理员登录态，提供文字/语音会话、业务工具调用、确认后取消订单及人工工单。可查询真实商品、订单和物流；不会在缺少知识库时编造售后政策。完整协议见 [Agent 接口文档](../docs/AGENT-API.md)。

## 数据与业务约束

### 订单状态和库存事务

正常履约顺序为：

```text
pending_payment → paid_pending_shipment → in_delivery → completed
```

`cancelled`、`refunded` 为终态，允许的分支以订单状态规则为准。

- 创建订单预留库存；取消仍处于预留状态的订单时释放预留。
- 保存运单和取得电子面单不出库；确认实物交接时，在同一事务中更新订单、预留、库存流水和时间线。
- 一单多包裹按整单统一交接，只出库一次；任一包裹变更都会更新整单交接版本。
- 重试受幂等键和版本号保护；面单申请中或结果待核实时，先核实外部结果再继续相关操作。
- 实际运费是物流支出，独立于订单向客户收取的 `shipping_fee`；未结算与零元分别记录，修订保留来源和审计记录。
- 订单和面单打印均先校验当前状态；申请打开打印页不等同于收到打印成功回执。

### 数据关系与历史初始化

客户关联多个地址和订单，订单关联多个商品行及包裹。订单保留收件地址、商品名称和价格快照；地址簿或商品记录后续变化不改写原始订单快照。

服务启动时创建缺失表、兼容旧订单状态，并执行幂等的商品种子和运营期初初始化：空商品表初始化 12 条基础样本，首次接入补充 100 条中文商品；不覆盖已有商品，不重复扣减历史库存，不猜测未知的付款或交接时间。新增物流及包裹表的规则分别见各期文档。

## 目录结构与开发约定

```text
backend/
├─ app/
│  ├─ main.py             # 应用入口、CORS 和后台维护任务
│  ├─ api/                # 汇总功能路由，挂载 /api
│  ├─ core/               # 运行配置与环境变量
│  ├─ db/                 # 数据库引擎、请求 Session 和启动初始化
│  └─ features/
│     ├─ auth/            # 管理员、密码和会话
│     ├─ products/        # 商品、图片和商品种子
│     ├─ customers/       # 客户与地址簿
│     ├─ orders/          # 订单、商品行、快照和状态事务
│     ├─ operations/      # 库存、收款、时间线、打印和经营指标
│     ├─ shipping/        # 发货、包裹、面单、运费、物流和地图
│     └─ agent/           # 客服会话、模型、工具和工单
├─ scripts/              # 数据维护和测试数据脚本
├─ tests/                # 隔离回归与并发测试
├─ deploy/nginx/         # 高德代理配置模板
├─ uploads/              # 本地上传文件，忽略版本管理
└─ start-dev.ps1         # Windows 开发启动脚本
```

接口路由负责 HTTP 输入校验和响应，CRUD 或服务模块负责持久化与业务事务。商品列表请求主要遵循 `catalog/router.py → catalog/crud.py → 数据库`，详情维护由 `details/` 处理，表结构升级与演示数据归入 `bootstrap/`，根 `router.py` 统一汇总商品路由。文件职责与依赖约定见[商品目录说明](app/features/products/README.md)。跨订单、库存和物流的动作在对应业务服务中协调。

`shipping/` 按 `delivery`（地图配送）、`fulfillment`（运单和交接）、`parcels`（包裹和运费）、`labels`（电子面单）、`tracking`（物流轨迹）分目录，由一个根路由统一汇总。文件职责与依赖约定见 [发货物流目录说明](app/features/shipping/README.md)。

`aftersales/` 管理售后、实际退款和退件验收，`inventory/` 管理盘点、报损与入库更正；共用原有订单事务和库存流水。流程、模块边界及新增接口见 [平台业务闭环优化](../docs/PLATFORM-OPTIMIZATION.md)。

API 字段和筛选值保持语言无关，显示名称及翻译由前端处理。业务方法统一提交事务，请求 Session 关闭时回滚未提交的修改。

## 测试与测试数据

### 默认回归测试

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认使用隔离 SQLite 数据库，覆盖认证、订单、库存、打印、客服、物流、多包裹、面单和运费逻辑；平台调用在相关测试中使用替身，不真实取号或扣费。

### MySQL 并发测试

```powershell
$env:RUN_MYSQL_TESTS = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:RUN_MYSQL_TESTS
```

测试使用 `DATABASE_URL` 的服务器凭证创建随机命名的 `bugfix_test_*` 隔离库，并在结束后清理；账号需要建库和删库权限，不向应用业务库写入测试数据。覆盖并发状态变更、交接重试、物流查询竞争及运单/包裹修改冲突。

### 带标记的业务测试数据

先预览计划，不写入数据：

```powershell
.venv\Scripts\python.exe scripts/seed_core_test_data.py --batch TEST-20261028 --dry-run
```

确认需要写入当前配置的数据库后，移除 `--dry-run`。脚本在一个外层事务中创建独立商品、客户及 100 个标记订单，同批次只插入一次，不使用现有商品。

`TEST-20261028` 包含待付款 20 单、待发货 25 单、配送中 15 单、已完成 25 单、已取消 10 单、已退款 5 单。在订单工作台搜索批次号可查看。模拟时间和收款均带测试标记，事件元数据保留实际注入时间。

创建的 ID 记录在 `operation_settings` 的 `test_seed:TEST-20261028` 以及项目根目录本地忽略的 `.local-test/seed-TEST-20261028.json` 中，供后续核对和清理；保留原有经营指标统计覆盖边界。

## 相关文档

- [项目入口](../README.md)与[前端说明](../frontend/README.md)
- [核心模块需求](../docs/PRD-core-modules-v1.md)与[实现说明](../docs/IMPLEMENTATION-core-modules.md)
- [发货基础流程、库存交接和历史补录](../docs/SHIPPING-phase1.md)
- [物流查询与后台轮询](../docs/SHIPPING-phase2.md)
- [电子面单、批量发货、多包裹与实际运费](../docs/SHIPPING-phase3.md)
- [Agent 接口协议与服务配置](../docs/AGENT-API.md)
