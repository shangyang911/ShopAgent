"""整单包裹轨迹汇总与附加包裹后台轮询。"""
from fastapi import HTTPException
from sqlalchemy import or_, select

from app.features.operations.models import utcnow
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.parcels.models import ExtraParcel
from .parcel_models import ParcelTrackingModel
from .summary import summarize


def package_tracking_snapshot(db, order_id, settings=None):
    from .service import tracking_snapshot
    # 保留一号包裹的旧快照契约，并按实际包裹清单追加各自进度。
    main = tracking_snapshot(db, order_id, settings)
    numbers = [1] if main["tracking_number"] else []
    numbers.extend(db.scalars(select(ExtraParcel.package_no).where(ExtraParcel.order_id == order_id).order_by(ExtraParcel.package_no)))
    items = [main if number == 1 else tracking_snapshot(db, order_id, settings, package_no=number) for number in numbers]
    # 物流汇总只是平台事实，不能据此自动改订单完成状态。
    return {"order_id": order_id, "items": items, **summarize([row["status"] for row in items])}


async def poll_extra_packages(factory, settings, limit=20):
    from .service import sync_tracking
    if not (settings.configured and settings.auto_sync) or limit <= 0:
        return 0
    with factory() as db:
        candidates = list(db.execute(select(ExtraParcel.order_id, ExtraParcel.package_no)
            .join(ShipmentModel, ShipmentModel.order_id == ExtraParcel.order_id)
            .outerjoin(ParcelTrackingModel, (ParcelTrackingModel.order_id == ExtraParcel.order_id) & (ParcelTrackingModel.package_no == ExtraParcel.package_no))
            .where(or_(ShipmentModel.dispatched_at.is_not(None), ShipmentModel.historical == True),
                   or_(ParcelTrackingModel.status.is_(None), ParcelTrackingModel.status != "signed"),
                   or_(ParcelTrackingModel.next_sync_at.is_(None), ParcelTrackingModel.next_sync_at <= utcnow()))
            .order_by(ParcelTrackingModel.last_attempt_at.is_not(None), ParcelTrackingModel.last_attempt_at, ExtraParcel.order_id, ExtraParcel.package_no).limit(limit)))
    for order_id, package_no in candidates:
        # 每个包裹独立 Session 和失败边界；某个运单错误不会阻断其他包裹。
        with factory() as db:
            try:
                await sync_tracking(db, order_id, settings, package_no=package_no)
            except HTTPException:
                db.rollback()
    return len(candidates)
