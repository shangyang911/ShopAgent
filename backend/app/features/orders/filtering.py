"""工作台筛选视图独立于订单履约状态，列表、计数和导出共用口径。"""
from typing import Literal

from sqlalchemy import func, or_, select

from app.features.aftersales.models import AfterSale, RefundRecord
from .models import OrderModel, OrderStatus

# 售后处理中是查询视图，不写入订单状态字段。
OrderFilterStatus = OrderStatus | Literal["aftersale_pending"]


def status_condition(value):
    """退款与履约允许重叠：完成后退款仍保留完成事实，同时进入退款视图。"""
    if value == "aftersale_pending":
        return select(AfterSale.id).where(AfterSale.order_id == OrderModel.id, AfterSale.status == "open").exists()
    if value == OrderStatus.REFUNDED:
        # EXISTS 不放大行数，一单多次退款仍只计一单；兼容历史仅标记退款的订单。
        recorded = select(RefundRecord.id).where(RefundRecord.order_id == OrderModel.id).exists()
        return or_(OrderModel.status == OrderStatus.REFUNDED, recorded)
    return OrderModel.status == value


def status_counts(db, order_ids):
    counts = dict(db.execute(select(OrderModel.status, func.count()).where(OrderModel.id.in_(order_ids)).group_by(OrderModel.status)).all())
    result = {key.value: count for key, count in counts.items()}
    # 退款/售后数量不能从履约状态分组推导，也不能加总后当成全部订单数。
    for view in ("refunded", "aftersale_pending"):
        count = db.scalar(select(func.count()).select_from(OrderModel).where(OrderModel.id.in_(order_ids), status_condition(view))) or 0
        if count:
            result[view] = count
    return result
