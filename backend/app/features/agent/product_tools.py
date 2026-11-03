# [商品查询工具] 每行对应具体SKU，价格与库存均读取规格表，禁止用商品汇总值冒充。
from sqlalchemy import func, or_, select
from app.features.products.models import ProductModel, ProductSKU
from app.features.products.constants import SELLABLE_STATUSES


def search_products(db, query='', product_id=None, sku_code='', color='', size='', active_only=True, limit=10):
    # 名称/编码支持模糊匹配；颜色、尺寸与明确SKU编码采用精确筛选。
    statement = select(ProductSKU, ProductModel).join(ProductModel, ProductModel.id == ProductSKU.product_id)
    if query.strip():
        statement = statement.where(or_(ProductModel.name.contains(query.strip(), autoescape=True), ProductSKU.code.contains(query.strip(), autoescape=True)))
    if product_id:
        statement = statement.where(ProductModel.id == product_id)
    for column,value in ((ProductSKU.code,sku_code),(ProductSKU.color,color),(ProductSKU.size,size)):
        if value.strip():
            statement = statement.where(func.lower(column) == value.strip().lower())
    if active_only:
        # 零库存规格仍可查询并标记缺货；只排除停用规格与不可售商品。
        statement = statement.where(ProductSKU.active.is_(True), ProductModel.status.in_(SELLABLE_STATUSES))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    # 总数和价格范围基于全部匹配结果，返回明细受limit限制，截断时提示继续缩小范围。
    rows = db.execute(statement.order_by(ProductModel.id, ProductSKU.id).limit(limit)).all()
    low, high = db.execute(statement.with_only_columns(func.min(ProductSKU.price), func.max(ProductSKU.price))).one()
    return {'items': [{'id': p.id, 'product_id': p.id, 'name': p.name, 'sku_id': sku.id, 'sku_code': sku.code,
        'color': sku.color, 'size': sku.size, 'price': str(sku.price), 'available_stock': sku.stock,
        'reserved_stock': sku.reserved, 'on_hand_stock': sku.stock+sku.reserved, 'sku_active': sku.active,
        'status': p.status, 'sellable': sku.active and p.status in SELLABLE_STATUSES and sku.stock>0,
        'stock_status': 'out_of_stock' if sku.stock == 0 else 'available',
        'href': f'/products/detail?id={p.id}'} for sku,p in rows], 'total': total, 'returned': len(rows),
        'truncated': total>len(rows), 'unit': 'SKU', 'matched_price_range': {'min':str(low) if low is not None else None, 'max':str(high) if high is not None else None},
        'notice': '每行对应具体SKU，价格为该规格价格，可售已扣预留。不同规格库存不得相加后冒充指定规格库存；默认只查启用且商品可售的规格。无结果时请澄清商品名、编码、颜色或尺寸，不得根据商品聚合库存猜测。'}
