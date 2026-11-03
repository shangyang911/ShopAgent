"""Decimal aggregates over verified business facts, in Shanghai natural days."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.orders.models import OrderModel, OrderStatus
from .common import utc_input, wire
from .inventory import inventory_query
from .models import OrderFacts, Receipt, Setting, utcnow
from app.features.aftersales.models import RefundRecord

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_current_user)])


def midnight(day):
    return datetime.combine(day, time(), SHANGHAI).astimezone(timezone.utc).replace(tzinfo=None)


def stats(db, start, end):
    amount, receipts = db.execute(select(func.coalesce(func.sum(Receipt.amount), 0), func.count(Receipt.order_id)).where(Receipt.occurred_at >= start, Receipt.occurred_at < end)).one()
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    # 退款按实际发生日统计；历史状态标记没有可靠金额，不推算进本指标。
    refunds = db.scalar(select(func.coalesce(func.sum(RefundRecord.amount), 0)).where(RefundRecord.occurred_at >= start, RefundRecord.occurred_at < end)) or Decimal("0.00")
    created = db.scalar(select(func.count()).select_from(OrderFacts).where(OrderFacts.created_at >= start, OrderFacts.created_at < end)) or 0
    completed = db.scalar(select(func.count()).select_from(OrderFacts).where(OrderFacts.completed_at >= start, OrderFacts.completed_at < end)) or 0
    return {"confirmed_receipt_amount": amount, "confirmed_receipt_order_count": receipts,
            "recorded_refund_amount": refunds, "net_confirmed_amount": amount - refunds,
            "created_order_count": created, "completed_order_count": completed,
            "average_confirmed_order_amount": (amount / receipts).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if receipts else None}


def local_day_expression(db, field):
    if db.bind.dialect.name == "sqlite":
        return func.date(func.datetime(field, "+8 hours"))
    return func.date(func.date_add(field, text("INTERVAL 8 HOUR")))


def trend_days(db, start, end):
    days = {}
    day = local_day_expression(db, Receipt.occurred_at)
    for value, amount, count in db.execute(select(day, func.sum(Receipt.amount), func.count(Receipt.id)).where(Receipt.occurred_at >= start, Receipt.occurred_at < end).group_by(day)):
        days.setdefault(str(value), {}).update(confirmed_receipt_amount=Decimal(str(amount)), confirmed_receipt_order_count=count)
    for field, name in [(OrderFacts.created_at, "created_order_count"), (OrderFacts.completed_at, "completed_order_count")]:
        day = local_day_expression(db, field)
        for value, count in db.execute(select(day, func.count()).select_from(OrderFacts).where(field >= start, field < end).group_by(day)):
            days.setdefault(str(value), {})[name] = count
    return days


def dashboard_data(db, start_day, end_day, as_of=None, granularity="day"):
    """按上海自然日统计已核实的业务事实；未覆盖的历史区间返回未知而非伪造零值。"""
    now = utcnow()
    as_of = min(as_of or now, now)
    start, end = midnight(start_day), midnight(end_day)
    if start >= end or (end_day - start_day).days > 1096:
        raise HTTPException(422, "日期区间必须为 1 至 1096 天，结束日期为排他上界")
    if start >= as_of:
        raise HTTPException(422, "开始日期不能晚于当前日期")
    effective_end = min(end, as_of)
    coverage_row = db.get(Setting, "data_coverage_start")
    coverage = datetime.fromisoformat(coverage_row.value) if coverage_row else now
    comparison_start = start - (end - start)
    comparison_end = comparison_start + (effective_end - start)
    current = stats(db, start, effective_end)
    comparison = stats(db, comparison_start, comparison_end)
    reliability = "unknown" if effective_end <= coverage else "partial" if start < coverage else "complete"
    comparison_reliable = comparison_start >= coverage
    metrics = {}
    for key, value in current.items():
        previous = comparison[key] if comparison_reliable else None
        change, label = None, "无可靠对比数据"
        if reliability == "complete" and comparison_reliable and value is not None and previous is not None:
            if previous == 0:
                label = "持平" if value == 0 else "新增"
            else:
                change = ((Decimal(value) - Decimal(previous)) / Decimal(previous) * 100).quantize(Decimal("0.01"))
                label = f"{'+' if change > 0 else ''}{change}%"
        metrics[key] = {"value": value if reliability != "unknown" else None, "comparison_value": previous,
                        "change_rate": str(change) if change is not None else None, "change_label": label, "reliability": reliability}
    daily = trend_days(db, start, effective_end)
    buckets = {}
    day_cursor = start_day
    last_day = min(end_day, (as_of.replace(tzinfo=timezone.utc).astimezone(SHANGHAI)).date() + timedelta(days=1))
    while day_cursor < last_day:
        bucket_day = day_cursor - timedelta(days=day_cursor.weekday()) if granularity == "week" else day_cursor.replace(day=1) if granularity == "month" else day_cursor
        key = bucket_day.isoformat()
        row = buckets.setdefault(key, {"label": key, "start_at": max(start, midnight(day_cursor)), "end_at": midnight(day_cursor + timedelta(days=1)),
                                      "confirmed_receipt_amount": Decimal("0.00"), "confirmed_receipt_order_count": 0,
                                      "created_order_count": 0, "completed_order_count": 0, "reliability": "complete"})
        row["end_at"] = min(midnight(day_cursor + timedelta(days=1)), effective_end)
        if midnight(day_cursor + timedelta(days=1)) <= coverage:
            row["reliability"] = "unknown" if row["reliability"] in ("complete", "unknown") else "partial"
        elif midnight(day_cursor) < coverage:
            row["reliability"] = "partial"
        elif row["reliability"] == "unknown":
            row["reliability"] = "partial"
        values = daily.get(day_cursor.isoformat(), {})
        for name in ("confirmed_receipt_amount", "confirmed_receipt_order_count", "created_order_count", "completed_order_count"):
            row[name] += values.get(name, 0)
        day_cursor += timedelta(days=1)
    for row in buckets.values():
        if row["reliability"] == "unknown":
            for name in ("confirmed_receipt_amount", "confirmed_receipt_order_count", "created_order_count", "completed_order_count"):
                row[name] = None
    pending = select(OrderModel.id).outerjoin(OrderFacts, OrderModel.id == OrderFacts.order_id).where(OrderModel.status == OrderStatus.PAID_PENDING_SHIPMENT)
    count_query = lambda query: db.scalar(select(func.count()).select_from(query.subquery())) or 0
    todos = {"pending_shipment": count_query(pending),
             "overdue_shipment": count_query(pending.where(OrderFacts.paid_at <= as_of - timedelta(hours=24))),
             "unknown_payment_time": count_query(pending.where(OrderFacts.paid_at.is_(None))),
             "low_stock": count_query(inventory_query(db, severity="low_stock")),
             "out_of_stock": count_query(inventory_query(db, severity="out_of_stock"))}
    return wire({"period_start": start, "period_end": end, "effective_end": effective_end, "as_of": as_of,
                 "comparison_start": comparison_start, "comparison_end": comparison_end, "currency": "CNY",
                 "timezone": "Asia/Shanghai", "data_coverage_start": coverage, "metrics": metrics,
                 "trend": list(buckets.values()), "todos": todos, "granularity": granularity})


@router.get("")
def overview(start_date: date, end_date: date, as_of: datetime | None = None,
             granularity: Literal['day', 'week', 'month'] = "day", db: Session = Depends(get_db)):
    return dashboard_data(db, start_date, end_date, utc_input(as_of), granularity)
