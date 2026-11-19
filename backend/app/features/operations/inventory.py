# [库存账本] 调用方持有商品锁并提交整笔事务，本模块负责数量变化、流水和提醒评估。
"""Stock ledger and alerts. Callers hold product locks and commit the whole business action."""
from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.features.products.models import ProductModel
from app.features.products.constants import SELLABLE_STATUSES
from .common import actor_fields, utc_input, wire
from .models import InventoryRule, Movement, Reservation, Setting, StockAlert, utcnow

def default_threshold(db, current=False):
    """页面可读查询快照；库存写入评估告警时使用最新设置，调用方先持有商品锁。"""
    statement = select(Setting).where(Setting.key == "default_stock_threshold")
    if current:
        statement = statement.with_for_update(read=True).execution_options(populate_existing=True)
    row = db.scalar(statement)
    return int(row.value) if row else 5


def rule_values(db, product_id):
    rule = db.scalar(select(InventoryRule).where(InventoryRule.product_id == product_id).with_for_update(read=True).execution_options(populate_existing=True))
    override = rule.threshold_override if rule else None
    return {"monitor_enabled": rule.monitor_enabled if rule else True,
            "threshold_override": override,
            "effective_threshold": override if override is not None else default_threshold(db, current=True)}


def evaluate_alert(db, product, now=None):
    """库存或规则改变后更新告警；恢复库存关闭旧告警，再次缺货生成新记录。"""
    now = now or utcnow()
    rule = rule_values(db, product.id)
    current = db.scalar(select(StockAlert).where(StockAlert.active_product_id == product.id).with_for_update().execution_options(populate_existing=True))
    monitored = rule["monitor_enabled"] and product.status in SELLABLE_STATUSES
    severity = "out_of_stock" if product.stock == 0 else "low_stock" if product.stock <= rule["effective_threshold"] else None
    if monitored and severity:
        if current is None:
            current = StockAlert(product_id=product.id, active_product_id=product.id,
                                 product_name_snapshot=product.name, severity=severity,
                                 available_at_trigger=product.stock, first_triggered_at=now,
                                 last_evaluated_at=now, alert_status="active")
            db.add(current)
        else:
            current.severity = severity
            current.last_evaluated_at = now
    elif current:
        current.alert_status = "resolved" if monitored else "closed"
        current.close_reason = "stock_recovered" if monitored else "monitor_disabled" if not rule["monitor_enabled"] else "product_unavailable"
        current.active_product_id = None
        current.resolved_at = now
        current.last_evaluated_at = now
    db.flush()


def reconcile_alerts(db):
    # 按商品 ID 加锁，与库存写入采用相同顺序；定时补偿和人工操作因此不会互相覆盖。
    for product in db.scalars(select(ProductModel).order_by(ProductModel.id).with_for_update().execution_options(populate_existing=True)):
        evaluate_alert(db, product)


def opening(db, product, actor=None, reserved=0):
    """只补记一次期初账，不再次扣库存；唯一键防止启动迁移重复生成流水。"""
    key = f"opening:product:{product.id}"
    if db.scalar(select(Movement.id).where(Movement.idempotency_key == key).with_for_update()) is None:
        db.add(Movement(product_id=product.id, product_name_snapshot=product.name,
                        movement_type="opening", available_before=0, available_delta=product.stock,
                        available_after=product.stock, reserved_delta=reserved, on_hand_delta=product.stock + reserved,
                        reference_type="product", reference_id=product.id, reference_number=str(product.id),
                        reason="期初账面结转；不代表实物盘点", idempotency_key=key, **actor_fields(actor)))
        db.flush()


def move(db, product, *, kind, available, reserved, reference_type, reference_id, reference_number, key, actor=None, reason=None, now=None, sku_id=None, check_alerts=True):
    """同时修改可用库存、追加流水和评估告警；由上层业务统一 commit。"""
    # 商品库存是各 SKU 可售量的汇总；两层余额与流水必须同事务更新，不能各自提交。
    # 在库 = 可用 + 预留。预留时一减一增，在库不变；发货时扣预留，在库才减少。
    if product.stock + available < 0:
        raise HTTPException(409, f"商品 {product.id} 可售库存不足")
    if product.stock + available > 2147483647:
        raise HTTPException(409, f"商品 {product.id} 库存超出可记录范围，整单未提交")
    from app.features.products.sku_inventory import apply_sku_movement
    sku = apply_sku_movement(db, product, available, reserved, sku_id)
    row = Movement(product_id=product.id, sku_id=sku.id, sku_code_snapshot=sku.code,
                   product_name_snapshot=product.name, movement_type=kind,
                   available_before=product.stock, available_delta=available, available_after=product.stock + available,
                   reserved_delta=reserved, on_hand_delta=available + reserved,
                   reference_type=reference_type, reference_id=reference_id, reference_number=reference_number,
                   reason=reason, occurred_at=now or utcnow(), idempotency_key=key, **actor_fields(actor))
    product.stock += available
    db.add(row)
    db.flush()
    if check_alerts:
        evaluate_alert(db, product, now)
    return row


def inventory_query(db, q="", category=None, severity=None):
    # 先按商品聚合预留量，再连接商品表，避免一件商品因多条预留记录被重复统计。
    q = q.strip()
    reserved = select(Reservation.product_id, func.sum(Reservation.quantity).label("quantity")).where(Reservation.status == "active").group_by(Reservation.product_id).subquery()
    threshold = func.coalesce(InventoryRule.threshold_override, default_threshold(db))
    statement = select(ProductModel, func.coalesce(reserved.c.quantity, 0), InventoryRule).outerjoin(reserved, reserved.c.product_id == ProductModel.id).outerjoin(InventoryRule, InventoryRule.product_id == ProductModel.id)
    if q:
        statement = statement.where(or_(ProductModel.name.contains(q, autoescape=True), ProductModel.id == int(q) if q.isdecimal() else False))
    if category:
        statement = statement.where(ProductModel.category == category)
    if severity:
        statement = statement.where(ProductModel.status.in_(SELLABLE_STATUSES), func.coalesce(InventoryRule.monitor_enabled, True) == True)
        if severity == "out_of_stock":
            statement = statement.where(ProductModel.stock == 0)
        elif severity == "low_stock":
            statement = statement.where(ProductModel.stock > 0, ProductModel.stock <= threshold)
        elif severity == "risk":
            statement = statement.where(ProductModel.stock <= threshold)
        elif severity == "normal":
            statement = statement.where(ProductModel.stock > threshold)
    return statement


def inventory_rows(db, statement, page, page_size):
    count = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = []
    default = default_threshold(db)
    for product, reserved, rule in db.execute(statement.order_by(ProductModel.stock, ProductModel.id).offset((page - 1) * page_size).limit(page_size)):
        threshold = rule.threshold_override if rule and rule.threshold_override is not None else default
        enabled = (rule.monitor_enabled if rule else True) and product.status in SELLABLE_STATUSES
        rows.append({"product_id": product.id, "name": product.name, "category": product.category,
                     "available_quantity": product.stock, "reserved_quantity": reserved, "on_hand_quantity": product.stock + reserved,
                     "monitor_enabled": rule.monitor_enabled if rule else True, "threshold_override": rule.threshold_override if rule else None,
                     "effective_threshold": threshold, "severity": ("out_of_stock" if product.stock == 0 else "low_stock" if product.stock <= threshold else "normal") if enabled else "unmonitored"})
    return {"items": rows, "total": count, "page": page, "page_size": page_size}


def paged(db, statement, page, page_size):
    count = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    return {"items": wire(list(db.scalars(statement.offset((page - 1) * page_size).limit(page_size)))), "total": count, "page": page, "page_size": page_size}


def movement_query(product_id=None, movement_type=None, reference_number="", actor="", start_at=None, end_at=None, order_id=None):
    """列表与导出共用筛选；时间范围采用左闭右开 [start, end)，避免跨日重复。"""
    start, end = utc_input(start_at), utc_input(end_at)
    if start and end and start >= end:
        raise HTTPException(422, "流水开始时间必须早于结束时间")
    statement = select(Movement).order_by(Movement.id.desc())
    for condition in [Movement.product_id == product_id if product_id else None,
                      Movement.movement_type == movement_type if movement_type else None,
                      Movement.reference_number.contains(reference_number, autoescape=True) if reference_number else None,
                      Movement.actor_name_snapshot == actor if actor else None,
                      Movement.occurred_at >= start if start else None, Movement.occurred_at < end if end else None]:
        if condition is not None:
            statement = statement.where(condition)
    if order_id:
        # 退件入库的直接凭据是售后单，仍属于原订单的库存闭环，详情和导出均应包含。
        from app.features.aftersales.models import AfterSale
        cases = select(AfterSale.id).where(AfterSale.order_id == order_id)
        statement = statement.where(or_(
            (Movement.reference_type == "order") & (Movement.reference_id == order_id),
            (Movement.reference_type == "aftersale") & Movement.reference_id.in_(cases),
        ))
    return statement
