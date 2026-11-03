"""附加包裹、运单唯一身份与实际运费记录。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.features.operations.models import utcnow


class ExtraParcel(Base):
    __tablename__ = "shipping_extra_parcels"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    package_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    carrier_name: Mapped[str] = mapped_column(String(80))
    tracking_number: Mapped[str] = mapped_column(String(80))
    remark: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WaybillIdentity(Base):
    __tablename__ = "shipping_waybill_identities"
    __table_args__ = (UniqueConstraint("order_id", "package_no", name="uq_waybill_package"),)
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    package_no: Mapped[int] = mapped_column(Integer)


class FreightRecord(Base):
    __tablename__ = "shipping_freight_records"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    package_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    source: Mapped[str] = mapped_column(String(250))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
