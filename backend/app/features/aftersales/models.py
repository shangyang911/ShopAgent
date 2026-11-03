"""售后只增加独立表，保留原订单、收款和库存流水的历史。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.features.operations.models import utcnow


class AfterSale(Base):
    __tablename__ = "aftersale_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # refund / return_refund
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    creation_key: Mapped[str] = mapped_column(String(100), unique=True)
    creation_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 验收和退款互不推导；先退款也不能伪装成已收到退件。
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime)
    inspected_by: Mapped[str | None] = mapped_column(String(80))
    inspection_note: Mapped[str | None] = mapped_column(Text)


class ReturnLine(Base):
    __tablename__ = "aftersale_return_lines"
    __table_args__ = (UniqueConstraint("case_id", "order_item_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("aftersale_cases.id"), index=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"))
    product_id: Mapped[int | None] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(180))  # 历史商品删除后仍可识别退件。
    quantity: Mapped[int] = mapped_column(Integer)
    accepted_quantity: Mapped[int | None] = mapped_column(Integer)
    damaged_quantity: Mapped[int | None] = mapped_column(Integer)


class RefundRecord(Base):
    """人工核实的实际退款记录；保存凭据不代表系统调用了支付机构。"""
    __tablename__ = "aftersale_refunds"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("aftersale_cases.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reference: Mapped[str] = mapped_column(String(250))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    recorded_by: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
