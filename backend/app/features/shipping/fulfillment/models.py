"""主包裹、整单交接版本与批量交接幂等记录。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.features.operations.models import utcnow
from app.features.shipping.tracking.models import ShipmentTrackingModel


class ShipmentModel(Base):
    __tablename__ = "order_shipments"
    __table_args__ = (UniqueConstraint("carrier_name", "tracking_number", name="uq_shipment_carrier_tracking"),)

    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    carrier_name: Mapped[str] = mapped_column(String(80))
    tracking_number: Mapped[str] = mapped_column(String(80))
    remark: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    historical: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dispatched_by: Mapped[str | None] = mapped_column(String(80))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime)
    tracking: Mapped["ShipmentTrackingModel | None"] = relationship(uselist=False, lazy="selectin")


class DispatchBatch(Base):
    __tablename__ = "shipping_dispatch_batches"
    idempotency_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
