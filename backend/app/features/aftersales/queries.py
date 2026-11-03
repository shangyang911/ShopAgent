"""售后只读快照集中组装，订单页和写操作返回同一契约。"""
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from app.features.operations.common import wire
from app.features.operations.models import Receipt
from app.features.orders.models import OrderModel
from .models import AfterSale, RefundRecord, ReturnLine


def refunded_total(db, order_id):
    return db.scalar(select(func.coalesce(func.sum(RefundRecord.amount), 0)).where(RefundRecord.order_id == order_id)) or Decimal("0.00")


def case_detail(db, case):
    # 明细与凭证分表存储；返回时才组合，避免用一段可覆盖 JSON 代替历史记录。
    return {**wire(case), "items": wire(list(db.scalars(select(ReturnLine).where(ReturnLine.case_id == case.id).order_by(ReturnLine.id)))),
            "refunds": wire(list(db.scalars(select(RefundRecord).where(RefundRecord.case_id == case.id).order_by(RefundRecord.id))))}


def order_snapshot(db, order_id):
    if db.get(OrderModel, order_id) is None:
        raise HTTPException(404, "订单不存在")
    receipt = db.scalar(select(Receipt).where(Receipt.order_id == order_id))
    paid, refunded = receipt.amount if receipt else Decimal("0.00"), refunded_total(db, order_id)
    # 没有可靠收款事实的历史订单不能按订单应收额假定已经收款。
    return wire({"order_id": order_id, "confirmed_paid": paid, "refunded_amount": refunded,
                 "refundable_amount": paid - refunded, "payment_verified": receipt is not None,
                 "items": [case_detail(db, row) for row in db.scalars(select(AfterSale).where(AfterSale.order_id == order_id).order_by(AfterSale.id.desc()))]})
