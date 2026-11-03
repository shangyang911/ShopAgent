"""草稿冻结库存快照，确认后只追加流水，不回写原入库单。"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.operations.common import digest, wire
from app.features.operations.inventory import move
from app.features.operations.models import Inbound, InboundLine, Movement, Reservation, utcnow
from app.features.products.models import ProductModel
from app.features.products.sku_inventory import resolve_sku, sku_payload
from .models import StockAdjustment, StockAdjustmentLine


def balances(db, product_id):
    # MySQL 可重复读下，普通聚合可能沿用加锁前的旧快照。
    # 调用方已锁商品，这里使用当前读核对预留和最新流水，识别数量先减后增的变化。
    reserved = sum(db.scalars(select(Reservation.quantity).where(Reservation.product_id == product_id, Reservation.status == "active").with_for_update()))
    version = db.scalar(select(Movement.id).where(Movement.product_id == product_id).order_by(Movement.id.desc()).limit(1).with_for_update()) or 0
    return reserved, version


def detail(db, row):
    return {**wire(row), "items": wire(list(db.scalars(select(StockAdjustmentLine).where(StockAdjustmentLine.adjustment_id == row.id).order_by(StockAdjustmentLine.product_id))))}


def create_adjustment(db, payload, actor):
    hashed = digest(sku_payload(payload))
    previous = db.scalar(select(StockAdjustment).where(StockAdjustment.creation_key == payload.idempotency_key))
    if previous:
        if previous.creation_hash != hashed:
            raise HTTPException(409, "提交键已用于其他调整内容")
        return detail(db, previous)
    source_lines = {}
    source_skus = set()
    if payload.source_inbound_id:
        source = db.scalar(select(Inbound).where(Inbound.id == payload.source_inbound_id).with_for_update())
        if not source or source.status != "confirmed":
            raise HTTPException(409, "只能更正已确认的入库单")
        source_lines = {line.product_id: line.quantity for line in db.scalars(select(InboundLine).where(InboundLine.receipt_id == source.id))}
        source_skus = {(line.product_id, line.sku_id) for line in db.scalars(select(InboundLine).where(InboundLine.receipt_id == source.id))}
    row = StockAdjustment(kind=payload.kind, reason=payload.reason, source_inbound_id=payload.source_inbound_id,
                          creation_key=payload.idempotency_key, creation_hash=hashed, created_by=actor.username)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # 两个相同草稿请求可能同时越过上面的读取；唯一键最终仲裁并发重试。
        db.rollback()
        previous = db.scalar(select(StockAdjustment).where(StockAdjustment.creation_key == payload.idempotency_key))
        if previous and previous.creation_hash == hashed:
            return detail(db, previous)
        raise HTTPException(409, "调整单提交冲突，请核对后重试")
    for item in sorted(payload.items, key=lambda item: item.product_id):
        product = db.scalar(select(ProductModel).where(ProductModel.id == item.product_id).with_for_update().execution_options(populate_existing=True))
        if not product:
            raise HTTPException(409, "商品已不存在，整单未保存")
        if source_lines and item.product_id not in source_lines:
            raise HTTPException(422, "更正商品不属于原入库单")
        _, version = balances(db, product.id)
        sku = resolve_sku(db, product, item.sku_id)
        if source_skus and (product.id, sku.id) not in source_skus and not ((product.id, None) in source_skus and sku.is_default):
            raise HTTPException(422, '更正 SKU 不属于原入库单')
        reserved = sku.reserved
        delta = item.counted_on_hand - reserved - sku.stock if payload.kind == "count" else item.available_delta
        if sku.stock + delta < 0:
            raise HTTPException(409, "调整后可售库存不能为负；实盘数量不能小于有效预留数量")
        db.add(StockAdjustmentLine(adjustment_id=row.id, product_id=product.id, sku_id=sku.id, product_name=product.name,
               available_before=sku.stock, reserved_before=reserved, movement_version=version,
               counted_on_hand=item.counted_on_hand, available_delta=delta))
    # 草稿仅保存预览，没有任何库存副作用。
    db.commit()
    return detail(db, row)


def confirm_adjustment(db, adjustment_id, actor):
    row = db.scalar(select(StockAdjustment).where(StockAdjustment.id == adjustment_id).with_for_update().execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404, "调整单不存在")
    if row.status == "confirmed":
        return detail(db, row)  # 重复确认不会再生成流水。
    if row.status != "draft":
        raise HTTPException(409, "作废单据不能确认")
    lines = list(db.scalars(select(StockAdjustmentLine).where(StockAdjustmentLine.adjustment_id == row.id).order_by(StockAdjustmentLine.product_id)))
    products = {}
    # 先核对所有商品和版本，再统一入账；中途失败由请求 Session 回滚整单。
    for line in lines:
        product = db.scalar(select(ProductModel).where(ProductModel.id == line.product_id).with_for_update().execution_options(populate_existing=True))
        if product is None:
            raise HTTPException(409, "Product no longer exists")
        _, version = balances(db, line.product_id)
        sku = resolve_sku(db, product, line.sku_id)
        reserved = sku.reserved
        if (sku.stock, reserved, version) != (line.available_before, line.reserved_before, line.movement_version):
            raise HTTPException(409, "草稿创建后库存已变化，请作废草稿并按最新库存重新核对")
        products[line.product_id] = product
    for line in lines:
        if line.available_delta:
            move(db, products[line.product_id], kind={"count": "stock_count", "damage": "stock_damage", "correction": "inbound_correction"}[row.kind],
                 available=line.available_delta, reserved=0, reference_type="adjustment", reference_id=row.id,
                 reference_number=f"ADJ-{row.id}", key=f"adjustment:{line.id}", actor=actor, reason=row.reason, sku_id=line.sku_id)
    row.status, row.confirmed_by, row.confirmed_at = "confirmed", actor.username, utcnow()
    db.commit()
    return detail(db, row)


def cancel_adjustment(db, adjustment_id):
    row = db.scalar(select(StockAdjustment).where(StockAdjustment.id == adjustment_id).with_for_update().execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404, "调整单不存在")
    if row.status == "confirmed":
        raise HTTPException(409, "已确认调整单只读，不能删除或作废")
    row.status = "cancelled"
    db.commit()
    return detail(db, row)
