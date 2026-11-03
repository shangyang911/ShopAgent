"""地图配置、配送路线和人工完成接口。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.delivery.schemas import DeliveryOrder, DeliveryRoute, DeliveryStatusUpdate, ShippingMapSettings
from app.features.shipping.delivery.service import AmapServiceError, AmapServiceUnavailable, ShippingService


router = APIRouter(prefix="/shipping", tags=["shipping"], dependencies=[Depends(require_current_user)])


@router.get("/map-settings", response_model=ShippingMapSettings)
def map_settings(db: Session = Depends(get_db)) -> ShippingMapSettings:
    """配送地图 / 初始化：返回默认中心点和缩放级别。"""
    return ShippingService(db).get_map_settings()


@router.get("/orders", response_model=list[DeliveryOrder])
async def delivery_orders(db: Session = Depends(get_db)) -> list[DeliveryOrder]:
    """配送工作台 / 列表：读取待发货与配送中订单并尝试解析地址坐标。"""
    return await ShippingService(db).list_delivery_orders_with_coordinates()


@router.get("/orders/{order_id}/route", response_model=DeliveryRoute)
async def delivery_route(order_id: int, db: Session = Depends(get_db)) -> DeliveryRoute:
    """配送工作台 / 路线：为指定订单解析收货地址并请求高德驾车规划。"""
    order = db.get(OrderModel, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    try:
        return await ShippingService(db).get_delivery_route(order)
    except AmapServiceError as error:
        code = status.HTTP_503_SERVICE_UNAVAILABLE if isinstance(error, AmapServiceUnavailable) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.patch("/orders/{order_id}/status", response_model=DeliveryOrder)
def update_delivery_status(
    order_id: int,
    payload: DeliveryStatusUpdate,
    db: Session = Depends(get_db),
    actor: UserModel = Depends(require_current_user),
) -> DeliveryOrder:
    """配送工作台 / 状态：只允许标记配送中或已送达。"""
    if payload.status not in {OrderStatus.IN_DELIVERY, OrderStatus.COMPLETED}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Status must be in_delivery or completed")
    if payload.status == OrderStatus.IN_DELIVERY:
        raise HTTPException(409, "请使用发货工作台的交接出库接口，核对运单版本后提交")
    order, error = OrderCRUD(db).update_status(order_id, payload.status, actor)
    if error:
        code = status.HTTP_404_NOT_FOUND if order is None else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=error)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    # 与配送队列共用响应模型读取同名字段，避免两处手工复制字段后发生遗漏。
    return DeliveryOrder.model_validate(order, from_attributes=True)
