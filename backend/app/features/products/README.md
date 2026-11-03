# 商品模块

按商品列表、详情维护和启动初始化分目录。`router.py` 是唯一的路由汇总入口，统一由 `app/api/router.py` 挂载。

```text
products/
├─ __init__.py
├─ router.py              # 汇总列表与详情路由
├─ constants.py           # 跨模块共用的可售状态
├─ models.py              # 商品、图片、图文内容和 SKU 数据表
├─ sku_inventory.py       # SKU 库存、默认规格与历史库存初始化
├─ storage.py             # 图片校验、上传、清理与 HTTP 错误转换
├─ favorites/             # 当前账号的商品收藏
│  ├─ __init__.py
│  ├─ models.py           # 账号和商品的唯一关联
│  └─ router.py           # 收藏列表、添加和取消
├─ catalog/               # 商品列表与基础信息管理
│  ├─ __init__.py
│  ├─ router.py           # 列表、筛选项、创建、更新、删除
│  ├─ schemas.py          # 基础字段、卡片、分页和筛选响应
│  ├─ crud.py             # 数据库筛选、排序与增删改事务
│  └─ state.py            # 商品业务状态与库存风险展示
├─ details/               # 商品详情与规格维护
│  ├─ __init__.py
│  ├─ router.py           # 详情、多图、图文、SKU 与库存分配接口
│  ├─ schemas.py          # 详情响应和带版本号的写入契约
│  └─ service.py          # 详情版本检查、SKU 维护与库存分配事务
└─ bootstrap/             # 启动时的数据准备
   ├─ __init__.py
   ├─ migration.py        # 历史表结构的增量升级
   └─ seed.py             # 基础样本与中文演示商品
```

## 调用约定

- 根路由依次挂载 `catalog.router`、`favorites.router`、`details.router`，确保 `/products/options` 和 `/products/favorites` 优先于 `/products/{product_id}` 匹配。
- 子包入口保持轻量；跨模块调用直接导入具体模块，例如 `products.catalog.crud.ProductCRUD`、`products.details.service.save_sku`。
- 列表与详情路由共用 `storage.store_uploaded_image`，统一图片大小、签名校验及 413/415 响应。
- 订单、库存和运营模块共用根目录的 `models`、`constants` 和 `sku_inventory`，SKU 库存仍加入调用方的锁与事务。
- `app/db/session.py` 调用 `bootstrap.migration` 和 `bootstrap.seed`；SKU 历史数据初始化仍由 `sku_inventory.initialize_product_skus` 负责。

现有 API 路径、请求响应、登录校验、数据库表和 `uploads/products/` 图片位置保持一致。

## 商品收藏

商品列表另支持 `batch/` 下的批量预览与确认，职责和事务边界见[运营功能说明](../../../../docs/OPERATIONS-ENHANCEMENTS.md)。

侧栏收藏页位于 `/favorites`。商品卡片爱心支持收藏和取消收藏，状态保存在后端当前账号下，刷新或重新登录后恢复。收藏展示最新商品数据，删除商品时同时清除对应收藏。首次启动时自动创建新增的 `product_favorites` 表。

- `GET /api/products/favorites`：当前账号收藏的商品，最近收藏优先。
- `PUT /api/products/favorites/{product_id}`：收藏，重复提交幂等；不存在的商品返回 404。
- `DELETE /api/products/favorites/{product_id}`：取消收藏，重复提交幂等。

以上接口均要求登录，不能读取或修改其他账号的收藏。

## 验证

在 `backend/` 执行默认隔离回归：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

MySQL 并发测试需显式启用，见[后端说明](../../../README.md#mysql-并发测试)。
