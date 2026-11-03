# 发货物流模块

按业务职责组织代码，`router.py` 汇总所有子路由，上层只需挂载一次。现有 `/api/shipping` 接口路径、请求响应、鉴权和数据库表保持不变。

```text
shipping/
├─ router.py                 # 模块唯一的路由汇总入口
├─ delivery/                 # 高德地图、地址解析、路线与人工完成
│  ├─ router.py
│  ├─ schemas.py
│  └─ service.py
├─ fulfillment/              # 主运单、发货工作台与整单交接
│  ├─ router.py              # 工作台、登记运单、单笔交接
│  ├─ batch_router.py        # 批量交接
│  ├─ schemas.py
│  ├─ models.py              # 主包裹与交接批次
│  └─ service.py
├─ parcels/                  # 多包裹与实际运费
│  ├─ router.py              # 包裹清单与附加包裹维护
│  ├─ freight_router.py      # 运费登记、汇总与导出
│  ├─ schemas.py
│  ├─ models.py
│  ├─ service.py             # 包裹修改、运单唯一性与运费登记
│  └─ freight.py             # 运费统计查询
├─ labels/                   # 电子面单
│  ├─ router.py
│  ├─ schemas.py
│  ├─ models.py              # 面单申请与核实任务
│  ├─ service.py
│  ├─ provider.py            # 快递100电子面单配置与客户端
│  └─ printing.py
└─ tracking/                 # 物流轨迹
   ├─ router.py
   ├─ schemas.py
   ├─ models.py
   ├─ service.py             # 缓存、同步、限频与后台轮询
   ├─ provider.py            # 快递100物流查询客户端
   ├─ settings.py
   ├─ context.py             # 主包裹与附加包裹查询上下文
   ├─ parcel_models.py       # 附加包裹轨迹与节点
   ├─ packages.py            # 整单签收汇总与附加包裹轮询
```

每个目录都有 `__init__.py`；包入口保持轻量，跨目录调用直接导入具体模块。新增接口放入所属业务的路由文件，再由根路由组合。

## 依赖与事务

- 订单模型和响应分别引用 `fulfillment.models.ShipmentModel`、`fulfillment.schemas.ShipmentRecord`；发货仍复用订单状态事务。
- 包裹清单由 `parcels.service` 汇总主包裹、附加包裹、面单与运费。整单版本和运单唯一性保护仍在原事务中执行。
- 面单服务调用运单或包裹登记方法时保留 `commit=False`，由面单服务提交完整事务。
- 物流查询归在 `tracking`，应用后台任务与客服直接调用对应服务。异常待办功能及其三张表的 ORM 映射已移除。
- `app/db/session.py` 显式导入包裹和面单模型注册数据表；主包裹和物流模型随订单模型导入。各目录的 `__init__.py` 不触发路由加载。
- `ShippingWorkbench` 响应模型紧邻工作台路由，避免订单响应和运单响应之间出现循环导入。

## 验证

在 `backend/` 执行：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认使用隔离 SQLite 和平台替身。MySQL 并发测试需要显式设置 `RUN_MYSQL_TESTS=1`，说明见[后端 README](../../../README.md#mysql-并发测试)。
