"""跨模块只读查询；标记已读不会关闭告警、推进订单或结束售后。"""
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import String, cast, func, literal, select, union_all
from app.features.auth.models import UserModel
from app.features.operations.models import OrderFacts, StockAlert, utcnow
from app.features.orders.models import OrderModel, OrderStatus
from app.features.aftersales.models import AfterSale
from app.features.products.models import ProductModel
from app.features.operations.inventory import inventory_query
from app.features.operations.common import wire
from .models import NotificationRead


def sources(db):
    """每个来源使用稳定标识去重；库存恢复再触发会产生新的告警 ID。"""
    # 同时核对当前库存规则，避免后台补偿尚未执行时展示已经恢复的旧告警。
    risk_products = inventory_query(db, severity="risk").with_only_columns(ProductModel.id)
    stock = select(
        (literal("stock:") + cast(StockAlert.id, String)).label("key"),
        literal("stock").label("category"), StockAlert.product_id.label("reference_id"),
        ProductModel.name.label("subject"), StockAlert.first_triggered_at.label("occurred_at"),
    ).join(ProductModel, ProductModel.id == StockAlert.active_product_id).where(
        StockAlert.alert_status == "active", ProductModel.id.in_(risk_products))
    shipment = select(
        (literal("shipment:") + cast(OrderModel.id, String)).label("key"),
        literal("shipment").label("category"), OrderModel.id.label("reference_id"),
        OrderModel.order_number.label("subject"), OrderFacts.paid_at.label("occurred_at"),
    ).join(OrderFacts, OrderFacts.order_id == OrderModel.id).where(
        OrderModel.status == OrderStatus.PAID_PENDING_SHIPMENT,
        OrderFacts.paid_at <= utcnow() - timedelta(hours=24))
    aftersale = select(
        (literal("aftersale:") + cast(AfterSale.id, String)).label("key"),
        literal("aftersale").label("category"), AfterSale.order_id.label("reference_id"),
        OrderModel.order_number.label("subject"), AfterSale.created_at.label("occurred_at"),
    ).join(OrderModel, OrderModel.id == AfterSale.order_id).where(AfterSale.status == "open")
    return union_all(stock, shipment, aftersale).subquery()


def list_notifications(db, user_id, category="all", state="all", page=1, page_size=20):
    source = sources(db)
    statement = select(source, NotificationRead.read_at).outerjoin(NotificationRead,
        (NotificationRead.source_key == source.c.key) & (NotificationRead.user_id == user_id))
    # 全局统计独立于分类、阅读筛选和分页，页面与导航共用同一统计结果。
    all_items = statement.subquery()
    active_count, read_count = db.execute(select(func.count(), func.count(all_items.c.read_at)).select_from(all_items)).one()
    unread_count = active_count - read_count
    if category != "all":
        statement = statement.where(source.c.category == category)
    if state != "all":
        statement = statement.where(NotificationRead.read_at.is_(None) if state == "unread" else NotificationRead.read_at.is_not(None))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(statement.order_by(source.c.occurred_at.desc(), source.c.key)
                      .offset((page - 1) * page_size).limit(page_size)).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        kind, ref = item["category"], item["reference_id"]
        item["title"] = {"stock": "库存待补货", "shipment": "收款超过 24 小时未发货", "aftersale": "售后待处理"}[kind]
        item["href"] = f"/inventory?tab=stock&q={ref}" if kind == "stock" else f"/orders?order_id={ref}"
        items.append(wire(item))
    return {"items": items, "total": total, "active_count": active_count,
            "unread_count": unread_count, "page": page, "page_size": page_size}


def mark_read(db, actor, keys):
    """锁账号串行化同一账号的重复点击；已读唯一键不会因重试重复插入。"""
    db.scalar(select(UserModel).where(UserModel.id == actor.id).with_for_update())
    unique_keys = set(keys)
    source = sources(db)
    valid = set(db.scalars(select(source.c.key).where(source.c.key.in_(unique_keys))))
    if valid != unique_keys:
        raise HTTPException(409, "部分待办已经处理或不存在，请刷新后重试")
    existing = set(db.scalars(select(NotificationRead.source_key).where(
        NotificationRead.user_id == actor.id, NotificationRead.source_key.in_(unique_keys)).with_for_update()))
    for key in unique_keys - existing:
        db.add(NotificationRead(user_id=actor.id, source_key=key))
    db.commit()


def mark_all_read(db, actor):
    """跨分类与分页标记当前全部待办；只修改当前账号的阅读记录。"""
    db.scalar(select(UserModel).where(UserModel.id == actor.id).with_for_update())
    source = sources(db)
    keys = set(db.scalars(select(source.c.key)))
    # 等待账号锁后使用当前读，防止并发重复提交插入相同的已读记录。
    existing = set(db.scalars(select(NotificationRead.source_key).where(
        NotificationRead.user_id == actor.id).with_for_update()))
    for key in keys - existing:
        db.add(NotificationRead(user_id=actor.id, source_key=key))
    db.commit()
