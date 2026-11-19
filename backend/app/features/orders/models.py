"""订单、订单商品及地址快照模型。"""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SqlEnum, Float, ForeignKey, Index, Integer, Numeric, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, column_property

from app.db.base import Base
from app.features.operations.models import OrderFacts, PrintDocument  # OrderFacts 注册字符串关系所需模型。
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.aftersales.models import AfterSale, RefundRecord

if TYPE_CHECKING:
    from app.features.customers.models import CustomerAddressModel, CustomerModel


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID_PENDING_SHIPMENT = "paid_pending_shipment"
    IN_DELIVERY = "in_delivery"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class OrderModel(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_status_created_at", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    address_id: Mapped[int | None] = mapped_column(ForeignKey("customer_addresses.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(SqlEnum(OrderStatus, native_enum=False, values_callable=lambda enum: [item.value for item in enum], length=32), nullable=False, default=OrderStatus.PENDING_PAYMENT)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 下单时复制收件信息，后续地址簿编辑/删除不影响配送单据。
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    province: Mapped[str] = mapped_column(String(80), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    district: Mapped[str] = mapped_column(String(80), nullable=False)
    address_detail: Mapped[str] = mapped_column(String(255), nullable=False)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    items: Mapped[list["OrderItemModel"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    customer: Mapped["CustomerModel"] = relationship()
    saved_address: Mapped["CustomerAddressModel | None"] = relationship(foreign_keys=[address_id])
    facts: Mapped["OrderFacts | None"] = relationship(uselist=False)
    shipment: Mapped["ShipmentModel | None"] = relationship(uselist=False, lazy="selectin")
    print_request_count = column_property(select(func.count(PrintDocument.id)).where(PrintDocument.order_id == id).correlate_except(PrintDocument).scalar_subquery())
    # 退款事实通过独立字段展示，不覆盖履约状态，也不让列表逐单查询售后接口。
    refunded_amount = column_property(select(func.coalesce(func.sum(RefundRecord.amount), 0)).where(RefundRecord.order_id == id).correlate_except(RefundRecord).scalar_subquery())
    aftersale_pending = column_property(select(func.count(AfterSale.id) > 0).where(AfterSale.order_id == id, AfterSale.status == "open").correlate_except(AfterSale).scalar_subquery())

    @property
    def paid_at(self):
        return self.facts.paid_at if self.facts else None

    @property
    def dispatch_started_at(self):
        return self.facts.dispatch_started_at if self.facts else None

    @property
    def completed_at(self):
        return self.facts.completed_at if self.facts else None

    @property
    def cancelled_at(self):
        return self.facts.cancelled_at if self.facts else None

    @property
    def created_time_verified(self):
        return bool(self.facts and self.facts.created_at)


class OrderItemModel(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    product_name: Mapped[str] = mapped_column(String(180), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sku_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    order: Mapped[OrderModel] = relationship(back_populates="items")
