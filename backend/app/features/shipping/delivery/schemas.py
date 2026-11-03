"""地图与配送接口的数据契约。"""
from decimal import Decimal

from pydantic import BaseModel, Field

from app.features.orders.models import OrderStatus


class ShippingMapSettings(BaseModel):
    center: tuple[float, float] = Field(description="Map center in AMap longitude, latitude order")
    zoom: int = Field(ge=3, le=18)


class DeliveryOrder(BaseModel):
    """配送响应统一字段契约；从订单同名属性读取，解析失败时仍保留条目与错误说明。"""

    id: int
    order_number: str
    status: OrderStatus
    total: Decimal
    recipient_name: str
    recipient_phone: str
    province: str
    city: str
    district: str
    address_detail: str
    longitude: float | None
    latitude: float | None
    geocode_error: str | None = None


class DeliveryRoute(BaseModel):
    """高德驾车路径，polyline 顺序为 [longitude, latitude]。"""

    order_id: int
    order_number: str
    origin: tuple[float, float]
    destination: tuple[float, float]
    distance_meters: int
    duration_seconds: int
    polyline: list[tuple[float, float]]


class DeliveryStatusUpdate(BaseModel):
    status: OrderStatus
