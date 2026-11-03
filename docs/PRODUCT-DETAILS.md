# 商品详情、SKU 与库存

商品列表点击图片或名称进入 `/products/detail?id={id}&return={商品列表地址}`。详情页提供手动多图轮播、放大、颜色/尺寸选择、所选 SKU 的价格和库存、数量限制，以及图文详情、规格参数、库存 Tab。管理区支持图片上传和排序、图文与参数编辑、SKU 新增/编辑、可售库存分配。

从详情页创建订单会带入商品、SKU 和数量，客户和收货地址仍由开单人员选择。库存中心补货与库存调整也可选择 SKU；订单详情、发货明细、配货打印及订单 CSV 保留规格信息。

## 接口

全部接口沿用管理员 Cookie 鉴权。请求和响应结构可在 `/docs` 的 `product-details` 分类查看。

- `GET /api/products/{id}`：商品、`revision`、轮播 `images`、全部图片 `media`、`skus`、图文 `description`、参数 `parameters`、颜色/尺寸 `options`。
- `POST /api/products/{id}/images`：multipart 字段 `image`、`expected_revision`，可选 `role=gallery|detail`、`alt`、`color`。仅接受不超过 5 MB 的 JPEG/PNG/WebP；轮播最多 20 张，总图片最多 100 张。
- `PUT /api/products/{id}/detail`：完整保存 `expected_revision`、有序 `gallery`、`description`、`parameters`。轮播条目为 `{id,alt,color}`；图文段落为 `{type:"text",text:...}` 或 `{type:"image",image_id:...}`。图片必须属于本商品，不接受原始 HTML。
- `DELETE /api/products/{id}/images/{image_id}?expected_revision=...`：删除未被详情使用的图片，保留主图；共享文件仍被引用时不删除文件。
- `POST /api/products/{id}/skus`：`code`、`color`、`size`、`price`、`active`，初始库存为 0。
- `PUT /api/products/{id}/skus/{sku_id}`：上述字段加 `expected_revision`；编码全局唯一，颜色/尺寸组合在商品内唯一。有库存或预留时不能停用。
- `POST /api/products/{id}/sku-stock-transfer`：`from_sku_id`、`to_sku_id`、`quantity`、`reason`、`idempotency_key`。仅分配可售库存，商品总库存和在库总数不变，双向流水留痕。

所有详情、图片和 SKU 写入均返回更新后的完整商品详情。过期版本或重复规格返回 409；非法字段或图片归属返回 422。

订单、入库和库存调整的商品行支持可选 `sku_id`。只有一个启用 SKU 时旧请求可省略；多规格时必须指定 SKU。库存调整仍沿用每单每商品一行，不同 SKU 可分别建立调整单。

## 库存口径与兼容

- `stock/available_quantity` 为未预留库存，`reserved_quantity` 为订单预留，`on_hand_quantity=available+reserved`。商品库存保持各 SKU 可售余额的汇总。
- 创建订单按 SKU 价格冻结快照并预留；取消释放原 SKU；实物交接扣原 SKU 预留；退货验收仅合格件恢复原 SKU。更改 SKU 价格或名称不会覆盖订单快照。
- 入库草稿和库存调整冻结所选 SKU；确认时复核库存版本。实际库存变更按商品锁、SKU 锁的顺序串行化，防止超卖。
- 原有商品对应一个默认 SKU，继承原库存和有效预留，不复制给新规格。旧图片迁入轮播，旧订单、流水和入库行只补充 SKU 关联，不改原金额、数量或时间。
- 新增 `product_skus`、`product_images`、`product_content` 三张业务表，原表只新增可空 SKU 字段。启动执行幂等升级；已移除的物流待办表不会重建。
- 数据库变更前备份位于 `.local-test/database-backups/before-product-skus-20261030T113124Z.sql`。

## 验证

后端：`RUN_MYSQL_TESTS=1` 后运行 `.venv/Scripts/python.exe -m unittest discover -s tests`，包括最后一件 SKU 的并发下单、分配幂等、规格库存、出库退货、图片归属、内容版本及迁移验证。

前端：`npm test`、`npm run build`、`npm run check:architecture`、`npm run format:check`。浏览器验收使用独立 SQLite 数据库，覆盖多图上传、颜色切图、规格价格、带 SKU 开单、补货和移动端布局。
