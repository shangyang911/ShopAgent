"""发货工作台、运单登记与单笔交接接口。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.orders.schemas import Order
from app.features.shipping.fulfillment.schemas import ShipmentDispatch, ShipmentRecord, ShipmentWrite
from app.features.shipping.fulfillment.service import save_shipment, workbench


router = APIRouter(prefix="/shipping", tags=["shipping"], dependencies=[Depends(require_current_user)])


class ShippingWorkbench(BaseModel):
    items: list[Order]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


@router.get("/workbench", response_model=ShippingWorkbench)
def shipping_workbench(status: Literal["paid_pending_shipment", "in_delivery", "completed"] | None = None,
                       keyword: str = Query(default="", max_length=180), page: int = Query(1, ge=1),
                       page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """快递工作台只读列表：不调用高德、不解析地址、不修改库存。"""
    return workbench(db, status=status, keyword=keyword, page=page, page_size=page_size)


@router.put("/orders/{order_id}/shipment", response_model=ShipmentRecord)
def record_shipment(order_id: int, payload: ShipmentWrite, db: Session = Depends(get_db),
                    actor: UserModel = Depends(require_current_user)):
    return save_shipment(db, order_id, payload, actor)


@router.post("/orders/{order_id}/dispatch", response_model=Order)
def dispatch_shipment(order_id: int, payload: ShipmentDispatch, db: Session = Depends(get_db),
                      actor: UserModel = Depends(require_current_user)):
    # 运单、状态、库存流水和时间线共享 update_status 的一次事务；经纬度不是前置条件。
    row, error = OrderCRUD(db).update_status(order_id, OrderStatus.IN_DELIVERY, actor,
        idempotency_key=payload.idempotency_key, shipment_revision=payload.expected_revision)
    if error:
        raise HTTPException(404 if row is None else 409, error)
    return row
