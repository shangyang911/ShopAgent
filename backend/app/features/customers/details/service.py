"""收款、退款独立聚合，避免订单行和退款连接后把金额重复累计。"""
from fastapi import HTTPException
from sqlalchemy import func, select
from app.features.customers.models import CustomerModel
from app.features.customers.schemas import Customer
from app.features.orders.models import OrderModel, OrderItemModel
from app.features.operations.models import OrderFacts, Receipt
from app.features.aftersales.models import RefundRecord
from app.features.operations.common import wire
from .models import CustomerProfile


def get_customer(db, customer_id, lock=False):
    query = select(CustomerModel).where(CustomerModel.id == customer_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "客户不存在")
    return row


def detail(db, customer_id, page=1, page_size=10):
    customer = get_customer(db, customer_id)
    ids = select(OrderModel.id).where(OrderModel.customer_id == customer_id)
    total = db.scalar(select(func.count()).select_from(ids.subquery())) or 0
    paid_count, paid_amount = db.execute(select(func.count(Receipt.id), func.coalesce(func.sum(Receipt.amount), 0))
        .where(Receipt.order_id.in_(ids))).one()
    refunded = db.scalar(select(func.coalesce(func.sum(RefundRecord.amount), 0)).where(RefundRecord.order_id.in_(ids)))
    last_purchase = db.scalar(select(func.max(Receipt.occurred_at)).where(Receipt.order_id.in_(ids)))
    # 未核实的历史业务时间保持未知，不用原订单 created_at 猜测购买时间。
    rows = db.execute(select(OrderModel.id, OrderModel.order_number, OrderModel.status, OrderModel.total,
                             OrderFacts.created_at, OrderFacts.paid_at)
        .outerjoin(OrderFacts, OrderFacts.order_id == OrderModel.id).where(OrderModel.customer_id == customer_id)
        .order_by(OrderModel.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings().all()
    common = db.execute(select(OrderItemModel.product_name, func.sum(OrderItemModel.quantity).label("quantity"))
        .join(OrderModel, OrderItemModel.order_id == OrderModel.id).join(Receipt, Receipt.order_id == OrderModel.id)
        .where(OrderModel.customer_id == customer_id).group_by(OrderItemModel.product_name)
        .order_by(func.sum(OrderItemModel.quantity).desc(), OrderItemModel.product_name).limit(5)).mappings().all()
    profile = db.get(CustomerProfile, customer_id)
    return {"customer": Customer.model_validate(customer).model_dump(),
            "summary": wire({"order_count": total, "paid_order_count": paid_count,
                "confirmed_amount": paid_amount, "refunded_amount": refunded,
                "net_confirmed_amount": paid_amount - refunded, "last_purchase_at": last_purchase}),
            "profile": {"tags": profile.tags if profile else [], "note": profile.note if profile else "", "revision": profile.revision if profile else 0},
            "common_products": [dict(row) for row in common],
            "orders": {"items": [wire(dict(row)) for row in rows], "total": total, "page": page, "page_size": page_size}}


def save_profile(db, customer_id, payload):
    get_customer(db, customer_id, lock=True)
    row = db.scalar(select(CustomerProfile).where(CustomerProfile.customer_id == customer_id)
                    .with_for_update().execution_options(populate_existing=True))
    revision = row.revision if row else 0
    if revision != payload.expected_revision:
        raise HTTPException(409, "客户跟进资料已变化，请刷新后再保存")
    if row is None:
        row = CustomerProfile(customer_id=customer_id)
        db.add(row)
    row.tags, row.note, row.revision = payload.tags, payload.note.strip(), revision + 1
    db.commit()
    return {"tags": row.tags, "note": row.note, "revision": row.revision}
