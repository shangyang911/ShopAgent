"""物流轨迹查询接口，继承现有管理员登录态。"""
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.shipping.tracking.service import sync_tracking, tracking_snapshot
from app.features.shipping.tracking.settings import TrackingSettings
from .packages import package_tracking_snapshot


router = APIRouter(prefix="/shipping", tags=["logistics"], dependencies=[Depends(require_current_user)])


@router.get("/orders/{order_id}/tracking-packages")
def all_package_tracking(order_id: int, db: Session = Depends(get_db)):
    return package_tracking_snapshot(db, order_id)


@router.get("/orders/{order_id}/packages/{package_no}/tracking")
def package_tracking(order_id: int, package_no: int = Path(ge=1, le=100), db: Session = Depends(get_db)):
    # 包裹号是查询条件的一部分，不能把主运单轨迹复制给其他包裹。
    return tracking_snapshot(db, order_id, package_no=package_no)


@router.post("/orders/{order_id}/packages/{package_no}/tracking/sync")
async def sync_package_tracking(order_id: int, package_no: int = Path(ge=1, le=100), db: Session = Depends(get_db)):
    return await sync_tracking(db, order_id, package_no=package_no)


@router.get("/tracking-service")
def tracking_service():
    return TrackingSettings.from_env().public()


@router.get("/orders/{order_id}/tracking")
def order_tracking(order_id: int, db: Session = Depends(get_db)):
    """普通刷新只读数据库缓存；显式点击同步才进入下面的 POST 接口。"""
    return tracking_snapshot(db, order_id)


@router.post("/orders/{order_id}/tracking/sync")
async def sync_order_tracking(order_id: int, db: Session = Depends(get_db)):
    """把手动同步交给业务层，复用后台轮询的限频与并发保护。"""
    return await sync_tracking(db, order_id)
