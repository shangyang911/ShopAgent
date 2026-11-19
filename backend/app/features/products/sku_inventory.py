"""SKU balances and product totals share the caller's product lock and transaction."""
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import select, text, update

from .models import ProductSKU

SKU_BACKFILL_KEY = 'migration:product_skus:v1'


def sku_payload(payload):
    value = payload.model_dump()
    for item in value.get('items', []):
        if item.get('sku_id') is None:
            item.pop('sku_id', None)
    return value


def ensure_default_sku(db, product):
    rows = list(db.scalars(select(ProductSKU).where(ProductSKU.product_id == product.id)
                          .order_by(ProductSKU.id).with_for_update().execution_options(populate_existing=True)))
    if rows:
        return rows
    from app.features.operations.models import Reservation
    reserved = sum(db.scalars(select(Reservation.quantity).where(
        Reservation.product_id == product.id, Reservation.status == 'active').with_for_update()))
    row = ProductSKU(product_id=product.id, code=f'P{product.id}-DEFAULT', color=product.color,
                     size='', price=product.price, stock=product.stock, reserved=reserved,
                     active=True, is_default=True, revision=1)
    db.add(row)
    db.flush()
    return [row]


def resolve_sku(db, product, sku_id=None, *, sellable=False):
    rows = ensure_default_sku(db, product)
    if sku_id is None:
        active = [row for row in rows if row.active]
        if not active:
            raise HTTPException(409, '该商品暂无可用 SKU')
        if len(active) != 1:
            raise HTTPException(409, '该商品有多个规格，请选择具体 SKU')
        row = active[0]
    else:
        row = next((row for row in rows if row.id == sku_id), None)
        if row is None:
            raise HTTPException(422, 'SKU 不属于该商品或已不存在')
    if sellable and not row.active:
        raise HTTPException(409, '该 SKU 已停用')
    return row


def apply_sku_movement(db, product, available, reserved, sku_id=None):
    row = resolve_sku(db, product, sku_id)
    if row.stock + available < 0 or row.reserved + reserved < 0:
        raise HTTPException(409, '所选 SKU 库存或预留不足，请刷新核对')
    if row.stock + available > 2147483647 or row.reserved + reserved > 2147483647:
        raise HTTPException(409, 'SKU 库存超出可记录范围')
    row.stock += available
    row.reserved += reserved
    row.revision += 1
    return row


def sku_label(row):
    return ' / '.join(value for value in (row.color, row.size) if value) or '默认规格'


def initialize_product_skus(db):
    """Run historical backfill once; normal product creation initializes its own SKU."""
    from app.features.operations.models import Setting
    if db.get(Setting, SKU_BACKFILL_KEY):
        return
    bind = db.get_bind()
    if bind.dialect.name != 'mysql':
        return _backfill_product_skus(db)
    # A separate connection retains the advisory lock across the backfill commit.
    with bind.connect() as connection:
        # MySQL limits advisory lock names to 64 characters, including long database names.
        key = 'sku-backfill-v1:' + sha256(bind.url.database.encode()).hexdigest()[:32]
        if connection.execute(text('SELECT GET_LOCK(:key, 30)'), {'key': key}).scalar_one() != 1:
            raise RuntimeError('商品 SKU 数据补齐锁获取失败')
        try:
            _backfill_product_skus(db)
        finally:
            connection.execute(text('SELECT RELEASE_LOCK(:key)'), {'key': key})


def _backfill_product_skus(db):
    from .models import ProductModel, ProductImage
    from app.features.orders.models import OrderItemModel
    from app.features.operations.models import Movement, InboundLine, Setting
    from app.features.inventory.models import StockAdjustmentLine
    # Locking reads see a marker committed by another starter while we waited.
    if db.get(Setting, SKU_BACKFILL_KEY, with_for_update=True, populate_existing=True):
        return
    for product in db.scalars(select(ProductModel).order_by(ProductModel.id).with_for_update()):
        if not db.scalar(select(ProductImage.id).where(ProductImage.product_id == product.id).limit(1)):
            db.add(ProductImage(product_id=product.id, url=product.image_url, alt=product.name, color=product.color, position=0, role='gallery'))
        rows = ensure_default_sku(db, product)
        default = next((row for row in rows if row.is_default), None)
        if default is None:
            continue
        db.execute(update(OrderItemModel).where(OrderItemModel.product_id == product.id, OrderItemModel.sku_id.is_(None))
                   .values(sku_id=default.id, sku_code=default.code, sku_label='默认规格'))
        db.execute(update(Movement).where(Movement.product_id == product.id, Movement.sku_id.is_(None))
                   .values(sku_id=default.id, sku_code_snapshot=default.code))
        for model in (InboundLine, StockAdjustmentLine):
            db.execute(update(model).where(model.product_id == product.id, model.sku_id.is_(None)).values(sku_id=default.id))
    db.add(Setting(key=SKU_BACKFILL_KEY, value='completed'))
    db.commit()
