"""配送业务：订单地址解析、起点配置和高德驾车路线查询。"""
import asyncio
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AMAP_SERVICE_PROXY_URL, AMAP_SHIPPING_ORIGIN_LATITUDE, AMAP_SHIPPING_ORIGIN_LONGITUDE, AMAP_WEB_KEY
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.delivery.schemas import DeliveryOrder, DeliveryRoute, ShippingMapSettings


# [配送地图 / 默认视野] 门店坐标未配置时保持全国视野，仅用于初始化地图。
DEFAULT_MAP_SETTINGS = ShippingMapSettings(center=(104.1954, 35.8617), zoom=5)
ACTIVE_DELIVERY_STATUSES = (OrderStatus.PAID_PENDING_SHIPMENT, OrderStatus.IN_DELIVERY)
DELIVERY_LIST_GEOCODE_BUDGET_SECONDS = 2.0


class AmapServiceError(Exception):
    """高德服务不可用、拒绝请求或未返回有效地址/路线时抛出。"""


class AmapServiceUnavailable(AmapServiceError):
    """代理或上游网络不可用，与订单地址不合法区分。"""


def _amap_error(payload: dict[str, Any], action: str) -> str:
    """保留高德错误码和说明，便于从地址解析界面定位 Key、权限或参数问题。"""
    code = str(payload.get("infocode") or "unknown")
    message = str(payload.get("info") or "高德未提供错误说明")
    return f"高德{action}失败（infocode={code}）：{message}"


def _origin() -> tuple[float, float] | None:
    """读取门店发货点；没有配置时不使用虚构坐标规划路线。"""
    if not AMAP_SHIPPING_ORIGIN_LONGITUDE or not AMAP_SHIPPING_ORIGIN_LATITUDE:
        return None
    try:
        longitude = float(AMAP_SHIPPING_ORIGIN_LONGITUDE)
        latitude = float(AMAP_SHIPPING_ORIGIN_LATITUDE)
    except ValueError:
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def _address_text(order: OrderModel) -> str:
    """将订单地址快照拼成适合高德地理编码的结构化地址。"""
    parts: list[str] = []
    for part in (order.province, order.city, order.district, order.address_detail):
        clean = part.strip()
        if clean and (not parts or clean != parts[-1]):
            parts.append(clean)
    return "".join(parts)


class ShippingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_map_settings(self) -> ShippingMapSettings:
        """门店起点配置后，地图初始视野自动定位到门店。"""
        origin = _origin()
        return ShippingMapSettings(center=origin or DEFAULT_MAP_SETTINGS.center, zoom=12 if origin else DEFAULT_MAP_SETTINGS.zoom)

    def list_delivery_orders(self, page: int = 1, page_size: int = 100) -> list[OrderModel]:
        """分页返回待发货与配送中的订单，便于开始配送和继续配送。"""
        statement = (
            select(OrderModel)
            .where(OrderModel.status.in_(ACTIVE_DELIVERY_STATUSES))
            .order_by(OrderModel.created_at.asc(), OrderModel.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.db.scalars(statement).all())

    async def _request_amap(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """所有 Web 服务请求经 Nginx 代理；代理侧添加安全密钥 jscode。"""
        if not AMAP_WEB_KEY:
            raise AmapServiceError("后端尚未配置高德 Web 服务 Key，请在 backend/.env 设置 AMAP_WEB_KEY。")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=4.0)) as client:
                response = await client.get(
                    f"{AMAP_SERVICE_PROXY_URL}/{path.lstrip('/')}",
                    params={**params, "key": AMAP_WEB_KEY, "output": "JSON"},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.ConnectError as error:
            message = "无法连接高德代理服务，请确认代理已启动且 AMAP_SERVICE_PROXY_URL 配置正确。"
            if urlsplit(AMAP_SERVICE_PROXY_URL).hostname in {"localhost", "127.0.0.1", "::1"}:
                message += "Windows 本地开发请在 backend 目录运行：.venv\\Scripts\\python.exe dev_amap_proxy.py，并保持运行。"
            raise AmapServiceUnavailable(message) from error
        except httpx.TimeoutException as error:
            raise AmapServiceUnavailable("高德服务请求超时，请检查代理网络连接后重试。") from error
        except httpx.HTTPError as error:
            raise AmapServiceUnavailable("高德代理或上游服务暂时不可用，请检查代理服务后重试。") from error
        except ValueError as error:
            raise AmapServiceUnavailable("高德代理未返回有效 JSON 数据，请检查代理配置。") from error
        if not isinstance(payload, dict):
            raise AmapServiceError("高德服务返回了无法识别的数据。")
        return payload

    async def resolve_order_coordinates(self, order: OrderModel) -> tuple[float, float]:
        """优先使用订单地址快照坐标；缺少坐标时调用高德地理编码并持久化。"""
        if order.longitude is not None and order.latitude is not None:
            return order.longitude, order.latitude

        address = _address_text(order)
        if not address:
            raise AmapServiceError("订单收货地址为空，无法解析坐标。")
        payload = await self._request_amap(
            "/v3/geocode/geo",
            {"address": address, "city": order.city},
        )
        if payload.get("status") != "1":
            raise AmapServiceError(_amap_error(payload, "地址解析"))
        geocodes = payload.get("geocodes") or []
        first_geocode = geocodes[0] if isinstance(geocodes, list) and geocodes else None
        if not isinstance(first_geocode, dict) or not first_geocode.get("location"):
            raise AmapServiceError("高德未能识别该收货地址，请先补充省、市、区和详细门牌信息。")
        try:
            longitude, latitude = (float(value) for value in first_geocode["location"].split(",", 1))
        except (AttributeError, TypeError, ValueError) as error:
            raise AmapServiceError("高德返回的收货坐标格式无效。") from error

        order.longitude = longitude
        order.latitude = latitude
        self.db.commit()
        return longitude, latitude

    async def list_delivery_orders_with_coordinates(self, page: int = 1, page_size: int = 100) -> list[DeliveryOrder]:
        """整页共用解析时限；超时保留地址快照，单笔路线接口仍可重试解析。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DELIVERY_LIST_GEOCODE_BUDGET_SECONDS
        rows = self.list_delivery_orders(page=page, page_size=page_size)
        result: list[DeliveryOrder] = []
        for order in rows:
            geocode_error = None
            if order.longitude is None or order.latitude is None:
                try:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    # 顺序解析，避免多个任务并发使用同一个 SQLAlchemy Session。
                    await asyncio.wait_for(self.resolve_order_coordinates(order), timeout=remaining)
                except TimeoutError:
                    geocode_error = "本页地址解析超时，请通过单笔订单路线重试。"
                except AmapServiceError as error:
                    geocode_error = str(error)
            delivery = DeliveryOrder.model_validate(order, from_attributes=True)
            delivery.geocode_error = geocode_error
            result.append(delivery)
        return result

    async def get_delivery_route(self, order: OrderModel) -> DeliveryRoute:
        """解析收货地址并返回高德驾车路线的起终点、摘要和折线点。"""
        if order.status not in ACTIVE_DELIVERY_STATUSES:
            raise AmapServiceError("只有待发货或配送中的订单可以规划路线。")
        origin = _origin()
        if origin is None:
            raise AmapServiceError("请先在后端配置门店发货坐标。")
        destination = await self.resolve_order_coordinates(order)
        payload = await self._request_amap(
            "/v3/direction/driving",
            {
                "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
                "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
                "extensions": "all",
            },
        )
        paths = (payload.get("route") or {}).get("paths") or []
        if payload.get("status") != "1":
            raise AmapServiceError(_amap_error(payload, "驾车路线规划"))
        if not isinstance(paths, list) or not paths or not isinstance(paths[0], dict):
            raise AmapServiceError("高德未能规划该订单的驾车路线，请检查地址坐标。")

        path = paths[0]
        polyline: list[tuple[float, float]] = [origin]
        try:
            for step in path.get("steps") or []:
                for point in (step.get("polyline") or "").split(";"):
                    if not point:
                        continue
                    longitude, latitude = (float(value) for value in point.split(",", 1))
                    coordinate = (longitude, latitude)
                    if coordinate != polyline[-1]:
                        polyline.append(coordinate)
        except (AttributeError, TypeError, ValueError) as error:
            raise AmapServiceError("高德返回的路线坐标格式无效。") from error
        if len(polyline) == 1:
            polyline.append(destination)
        else:
            polyline[-1] = destination

        try:
            distance_meters = int(float(path.get("distance") or 0))
            duration_seconds = int(float(path.get("duration") or 0))
        except (TypeError, ValueError) as error:
            raise AmapServiceError("高德返回的路线摘要数据无效。") from error
        return DeliveryRoute(
            order_id=order.id,
            order_number=order.order_number,
            origin=origin,
            destination=destination,
            distance_meters=distance_meters,
            duration_seconds=duration_seconds,
            polyline=polyline,
        )
