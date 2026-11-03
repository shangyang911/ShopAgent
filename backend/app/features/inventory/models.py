"""调整单冻结创建时的库存版本，确认时拒绝覆盖后续业务变化。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.features.operations.models import utcnow


class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # count / damage / correction
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    reason: Mapped[str] = mapped_column(Text)
    source_inbound_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_receipts.id"))
    creation_key: Mapped[str] = mapped_column(String(100), unique=True)
    creation_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_by: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class StockAdjustmentLine(Base):
    __tablename__ = "stock_adjustment_lines"
    __table_args__ = (UniqueConstraint("adjustment_id", "product_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    adjustment_id: Mapped[int] = mapped_column(ForeignKey("stock_adjustments.id"), index=True)
    product_id: Mapped[int] = mapped_column(Integer)
    sku_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_name: Mapped[str] = mapped_column(String(180))
    available_before: Mapped[int] = mapped_column(Integer)
    reserved_before: Mapped[int] = mapped_column(Integer)
    movement_version: Mapped[int] = mapped_column(Integer)
    counted_on_hand: Mapped[int | None] = mapped_column(Integer)
    available_delta: Mapped[int] = mapped_column(Integer)
    # 本单只改可售和在库，不挪用订单已预留的数量。
