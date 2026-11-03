"""Idempotent, additive opening migration. Never infer historical receipt/completion times."""
from sqlalchemy import select

from app.features.orders.models import OrderModel, OrderStatus
from app.features.products.models import ProductModel
from .common import add_event
from .inventory import opening, evaluate_alert
from .models import OrderFacts, Reservation, Setting, utcnow


def initialize_operations(db):
    """首次接入运营账本：保留历史库存，不猜付款时间，也不再次扣除历史订单预留。"""
    if db.get(Setting, "data_coverage_start"):
        return
    now = utcnow()
    reserved_by_product = {}
    for order in db.scalars(select(OrderModel).order_by(OrderModel.id).with_for_update()):
        if db.get(OrderFacts, order.id) is None:
            db.add(OrderFacts(order_id=order.id))
            add_event(db, order, "historical_import", "历史数据接入：原业务时间未经核实，保持未知", key=f"import:{order.id}", historical=True)
        if order.status in (OrderStatus.PENDING_PAYMENT, OrderStatus.PAID_PENDING_SHIPMENT):
            for item in order.items:
                if item.product_id is not None and db.scalar(select(Reservation.id).where(Reservation.order_item_id == item.id)) is None:
                    db.add(Reservation(order_id=order.id, order_item_id=item.id, product_id=item.product_id, quantity=item.quantity, status="active", created_at=now))
                    reserved_by_product[item.product_id] = reserved_by_product.get(item.product_id, 0) + item.quantity
    db.flush()
    for product in db.scalars(select(ProductModel).order_by(ProductModel.id).with_for_update()):
        opening(db, product, reserved=reserved_by_product.get(product.id, 0))
        evaluate_alert(db, product, now)
    db.add(Setting(key="data_coverage_start", value=now.isoformat()))
    if db.get(Setting, 'default_stock_threshold') is None:
        db.add(Setting(key='default_stock_threshold', value='5'))
    db.commit()
