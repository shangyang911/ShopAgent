from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.features.orders.models import OrderItemModel, OrderModel, OrderStatus
from app.features.orders.filtering import status_condition
from .models import OrderFacts, PrintDocument, utcnow


def order_sort_columns(sort):
    """列表和导出共用排序：未知创建时间排最后，同一时间用订单 ID 保证顺序稳定。"""
    if sort == "created_asc":
        return OrderFacts.created_at.is_(None), OrderFacts.created_at.asc(), OrderModel.id.asc()
    return OrderFacts.created_at.is_(None), OrderFacts.created_at.desc(), OrderModel.id.desc()


def order_query(db, status=None, statuses=None, keyword="", time_field="created_at", start_at=None, end_at=None,
                min_total=None, max_total=None, print_request_status=None, overdue=False, unknown_time=False, as_of=None):
    """只构建筛选 SQL，不分页；列表、状态计数和全量导出各自补充所需查询。"""
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(422, "开始时间必须早于结束时间")
    if min_total is not None and max_total is not None and min_total > max_total:
        raise HTTPException(422, "最低金额不能高于最高金额")
    if time_field not in ("created_at", "paid_at", "completed_at"):
        raise HTTPException(422, "不支持的时间字段")
    statement = select(OrderModel).outerjoin(OrderFacts, OrderFacts.order_id == OrderModel.id)
    if statuses:
        # 多选视图按 OR 合并，用 EXISTS 避免多笔退款导致重复订单和错误分页。
        statement = statement.where(or_(*(status_condition(value) for value in statuses)))
    elif status:
        statement = statement.where(status_condition(status))
    if keyword.strip():
        keyword = keyword.strip()
        exact_order = db.scalar(select(OrderModel.id).where(OrderModel.order_number == keyword))
        if exact_order:
            statement = statement.where(OrderModel.id == exact_order)
        else:
            matching_items = select(OrderItemModel.order_id).where(OrderItemModel.product_name.contains(keyword, autoescape=True))
            statement = statement.where(or_(OrderModel.recipient_phone == keyword,
                                            OrderModel.recipient_name.contains(keyword, autoescape=True),
                                            OrderModel.id.in_(matching_items)))
    field = getattr(OrderFacts, time_field)
    if start_at:
        statement = statement.where(field >= start_at)
    if end_at:
        statement = statement.where(field < end_at)
    if as_of:
        # Historical imports have no reliable creation time. Only verified records enter snapshot drills.
        statement = statement.where(field < as_of)
    if unknown_time:
        statement = statement.where(field.is_(None))
    if min_total is not None:
        statement = statement.where(OrderModel.total >= min_total)
    if max_total is not None:
        statement = statement.where(OrderModel.total <= max_total)
    if print_request_status:
        count = select(func.count(PrintDocument.id)).where(PrintDocument.order_id == OrderModel.id).correlate(OrderModel).scalar_subquery()
        if print_request_status == "never":
            statement = statement.where(count == 0)
        elif print_request_status == "requested":
            statement = statement.where(count > 0)
        elif print_request_status == "multiple":
            statement = statement.where(count > 1)
    if overdue:
        statement = statement.where(OrderModel.status == OrderStatus.PAID_PENDING_SHIPMENT,
                                    OrderFacts.paid_at <= (as_of or utcnow()) - timedelta(hours=24))
    return statement
