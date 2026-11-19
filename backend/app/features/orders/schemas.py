"""订单 API 请求、状态流转及响应结构。"""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.features.orders.models import OrderStatus
from app.features.shipping.fulfillment.schemas import ShipmentRecord


class OrderItemCreate(BaseModel):
    product_id: int = Field(gt=0)
    sku_id: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0, le=10000)


class OrderCreate(BaseModel):
    customer_id: int = Field(gt=0)
    address_id: int = Field(gt=0)
    items: list[OrderItemCreate] = Field(min_length=1, max_length=100)
    shipping_fee: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=10, decimal_places=2)
    note: str | None = Field(default=None, max_length=2000)


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    reason: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100)


class OrderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int | None
    product_name: str
    sku_id: int | None = None
    sku_code: str | None = None
    sku_label: str | None = None
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class Order(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_number: str
    customer_id: int
    address_id: int | None
    status: OrderStatus
    subtotal: Decimal
    shipping_fee: Decimal
    total: Decimal
    note: str | None
    recipient_name: str
    recipient_phone: str
    country: str
    province: str
    city: str
    district: str
    address_detail: str
    postal_code: str | None
    longitude: float | None
    latitude: float | None
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    dispatch_started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_time_verified: bool = False
    print_request_count: int = 0
    refunded_amount: Decimal = Decimal("0.00")
    aftersale_pending: bool = False
    logistics_summary: dict | None = None
    items: list[OrderItem]
    shipment: ShipmentRecord | None = None

    @field_serializer('created_at', 'updated_at', 'paid_at', 'dispatch_started_at', 'completed_at', 'cancelled_at')
    def serialize_time(self, value):
        from app.features.operations.common import wire
        return wire(value)


class OrderListResponse(BaseModel):
    items: list[Order]
    total: int
    page: int
    page_size: int
    status_counts: dict[str, int] = Field(default_factory=dict)


# 此表仅描述履约状态允许的方向，不代表退款已实际完成。
# refunded 保留给历史兼容；新退款由售后凭据登记，发货由工作台交接接口触发。
ORDER_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING_PAYMENT: {OrderStatus.PAID_PENDING_SHIPMENT, OrderStatus.CANCELLED},
    OrderStatus.PAID_PENDING_SHIPMENT: {OrderStatus.IN_DELIVERY, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.IN_DELIVERY: {OrderStatus.COMPLETED, OrderStatus.REFUNDED},
    OrderStatus.COMPLETED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}
