"""售后只读快照集中组装，订单页和写操作返回同一契约。"""
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from app.features.operations.common import wire
from app.features.operations.models import Receipt
from app.features.orders.models import OrderModel
from .models import AfterSale, RefundRecord, ReturnLine


def refunded_total(db, order_id, *, case_id=None, current=False):
    """只读页面使用快照汇总；写入校验须先锁订单，再请求最新退款事实。"""
    conditions = [RefundRecord.order_id == order_id]
    if case_id is not None:
        conditions.append(RefundRecord.case_id == case_id)
    if current:
        # 逐行当前读后再求和，避免 MySQL 可重复读沿用请求早先建立的快照。
        statement = select(RefundRecord.amount).where(*conditions)
        return sum(db.scalars(statement.order_by(RefundRecord.id).with_for_update()), Decimal("0.00"))
    return db.scalar(select(func.coalesce(func.sum(RefundRecord.amount), 0)).where(*conditions)) or Decimal("0.00")


def case_detail(db, case, *, current=False):
    # 明细与凭证分表存储；返回时才组合，避免用一段可覆盖 JSON 代替历史记录。
    items = select(ReturnLine).where(ReturnLine.case_id == case.id).order_by(ReturnLine.id)
    refunds = select(RefundRecord).where(RefundRecord.case_id == case.id).order_by(RefundRecord.id)
    if current:
        # 幂等重放在持有订单锁时也必须返回最新明细，不能混入早先的查询快照。
        items = items.with_for_update().execution_options(populate_existing=True)
        refunds = refunds.with_for_update().execution_options(populate_existing=True)
    return {**wire(case), "items": wire(list(db.scalars(items))), "refunds": wire(list(db.scalars(refunds)))}


def order_snapshot(db, order_id):
    if db.get(OrderModel, order_id) is None:
        raise HTTPException(404, "订单不存在")
    receipt = db.scalar(select(Receipt).where(Receipt.order_id == order_id))
    paid, refunded = receipt.amount if receipt else Decimal("0.00"), refunded_total(db, order_id)
    # 没有可靠收款事实的历史订单不能按订单应收额假定已经收款。
    return wire({"order_id": order_id, "confirmed_paid": paid, "refunded_amount": refunded,
                 "refundable_amount": paid - refunded, "payment_verified": receipt is not None,
                 "items": [case_detail(db, row) for row in db.scalars(select(AfterSale).where(AfterSale.order_id == order_id).order_by(AfterSale.id.desc()))]})
