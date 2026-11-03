"""Additive tables: existing order and product tables require no destructive migration."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Setting(Base):
    __tablename__ = "operation_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class OrderFacts(Base):
    __tablename__ = "order_facts"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    creation_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    creation_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)


class Receipt(Base):
    __tablename__ = "confirmed_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_name_snapshot: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40), default="manual_confirmation")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)


class OrderEvent(Base):
    __tablename__ = "order_events"
    __table_args__ = (UniqueConstraint("order_id", "sequence"), Index("ix_events_order_time", "order_id", "recorded_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_name_snapshot: Mapped[str] = mapped_column(String(80))
    actor_type: Mapped[str] = mapped_column(String(20))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)


class Reservation(Base):
    __tablename__ = "inventory_reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), unique=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)


class Movement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (Index("ix_movements_product_time", "product_id", "occurred_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer)
    sku_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku_code_snapshot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(180))
    movement_type: Mapped[str] = mapped_column(String(32))
    available_before: Mapped[int] = mapped_column(Integer)
    available_delta: Mapped[int] = mapped_column(Integer)
    available_after: Mapped[int] = mapped_column(Integer)
    reserved_delta: Mapped[int] = mapped_column(Integer)
    on_hand_delta: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[int] = mapped_column(Integer, index=True)
    reference_number: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_name_snapshot: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)


class InventoryRule(Base):
    __tablename__ = "inventory_rules"
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    threshold_override: Mapped[int | None] = mapped_column(Integer)


class StockAlert(Base):
    __tablename__ = "stock_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    active_product_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(180))
    severity: Mapped[str] = mapped_column(String(20))
    alert_status: Mapped[str] = mapped_column(String(20), default="active")
    available_at_trigger: Mapped[int] = mapped_column(Integer)
    first_triggered_at: Mapped[datetime] = mapped_column(DateTime)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    acknowledged_by: Mapped[str | None] = mapped_column(String(80))
    close_reason: Mapped[str | None] = mapped_column(String(80))


class RuleEvent(Base):
    __tablename__ = "inventory_rule_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(Integer, index=True)
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_name_snapshot: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Inbound(Base):
    __tablename__ = "inbound_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_number: Mapped[str] = mapped_column(String(50), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    remark: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_by: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    creation_key: Mapped[str] = mapped_column(String(100), unique=True)
    creation_hash: Mapped[str] = mapped_column(String(64))


class InboundLine(Base):
    __tablename__ = "inbound_receipt_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("inbound_receipts.id"), index=True)
    product_id: Mapped[int] = mapped_column(Integer)
    sku_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(180))
    quantity: Mapped[int] = mapped_column(Integer)


class PrintJob(Base):
    __tablename__ = "order_print_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(80))
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reprint_reason: Mapped[str | None] = mapped_column(Text)


class PrintDocument(Base):
    __tablename__ = "order_print_documents"
    __table_args__ = (UniqueConstraint("order_id", "attempt_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("order_print_jobs.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    document_revision: Mapped[int] = mapped_column(Integer)
    document_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
