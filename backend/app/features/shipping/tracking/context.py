"""为主包裹和附加包裹提供同一查询上下文，避免复制两套同步算法。"""
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import and_, select
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.parcels.models import ExtraParcel
from .models import ShipmentTrackingModel, TrackingEventModel
from .parcel_models import ParcelTrackingModel, ParcelTrackingEvent


def models_for(package_no):
    return (ShipmentTrackingModel, TrackingEventModel) if package_no == 1 else (ParcelTrackingModel, ParcelTrackingEvent)


def package_filter(model, order_id, package_no):
    # 一号包裹表沿用订单主键；新增表必须同时带包裹号，防止串读其他包裹。
    return and_(model.order_id == order_id, model.package_no == package_no) if package_no != 1 else model.order_id == order_id


def package_keys(order_id, package_no):
    return {"order_id": order_id, **({"package_no": package_no} if package_no != 1 else {})}


def shipment_for(db, order_id, package_no, *, lock=False):
    statement = select(ShipmentModel).where(ShipmentModel.order_id == order_id)
    main = db.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)
    if package_no == 1:
        return main
    statement = select(ExtraParcel).where(ExtraParcel.order_id == order_id, ExtraParcel.package_no == package_no)
    extra = db.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)
    if not main or not extra:
        raise HTTPException(404, "包裹不存在")
    # 交接和清单版本仍属于整单，快递公司与单号属于当前包裹。
    return SimpleNamespace(carrier_name=extra.carrier_name, tracking_number=extra.tracking_number,
                           revision=main.revision, dispatched_at=main.dispatched_at, historical=main.historical)
