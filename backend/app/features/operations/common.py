from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import select

from .models import OrderEvent, utcnow


def wire(value):
    """递归转换为接口数据：金额用字符串保精度，数据库 UTC 时间补 Z 标记。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if hasattr(value, "__table__"):
        return {c.name: wire(getattr(value, c.name)) for c in value.__table__.columns}
    return value


def digest(value):
    """同一业务内容生成稳定摘要，用来发现“同一幂等键、不同提交内容”的冲突。"""
    return sha256(json.dumps(wire(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def utc_input(value: datetime | None):
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(422, "时间必须包含时区，例如 +08:00 或 Z")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def actor_fields(actor):
    return {"actor_id": actor.id if actor else None, "actor_name_snapshot": actor.username if actor else "system"}


def add_event(db, order, kind, summary, actor=None, *, key, reason=None, before=None, details=None, occurred_at=None, historical=False):
    """追加订单事件，不改写历史；调用方须先锁订单，并统一提交整个业务事务。"""
    db.flush()
    details = {**(details or {}), "correlation_id": key}
    if before is not None:
        details["changed_fields"] = {"status": {"before": before, "after": order.status.value}}
    event = OrderEvent(
        order_id=order.id, sequence=(db.scalar(select(OrderEvent.sequence).where(OrderEvent.order_id == order.id).order_by(OrderEvent.sequence.desc()).limit(1).with_for_update()) or 0) + 1,
        event_type=kind, summary=summary, reason=reason,
        occurred_at=None if historical else (occurred_at or utcnow()), recorded_at=utcnow(),
        actor_type="migration" if historical else "user" if actor else "system",
        from_status=before, to_status=order.status.value if before is not None else None,
        details=wire(details), idempotency_key=key, **actor_fields(actor),
    )
    db.add(event)
    return event
