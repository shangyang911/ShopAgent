"""物流摘要按完整包裹清单计算，首包裹签收不代表整单全部签收。"""
from sqlalchemy import select

from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.parcels.models import ExtraParcel
from .models import ShipmentTrackingModel
from .parcel_models import ParcelTrackingModel


def summarize(states):
    signed, total = sum(state == "signed" for state in states), len(states)
    label = "全部包裹显示签收" if total and signed == total else "部分包裹显示签收" if signed else "尚无包裹确认签收"
    return {"package_count": total, "signed_count": signed, "summary": label}


def attach_summaries(db, orders):
    """整页批量读取两个包裹来源，不逐订单调用完整轨迹接口。"""
    if not orders:
        return orders
    ids = [order.id for order in orders]
    states = {order_id: [] for order_id in ids}
    for order_id, state in db.execute(select(ShipmentModel.order_id, ShipmentTrackingModel.status)
            .outerjoin(ShipmentTrackingModel, ShipmentTrackingModel.order_id == ShipmentModel.order_id)
            .where(ShipmentModel.order_id.in_(ids))):
        states[order_id].append(state)
    for order_id, state in db.execute(select(ExtraParcel.order_id, ParcelTrackingModel.status)
            .outerjoin(ParcelTrackingModel, (ParcelTrackingModel.order_id == ExtraParcel.order_id) & (ParcelTrackingModel.package_no == ExtraParcel.package_no))
            .where(ExtraParcel.order_id.in_(ids))):
        states[order_id].append(state)
    # 未同步包裹也进入分母，避免“只同步过的包裹都签收”被误判为整单签收。
    for order in orders:
        order.logistics_summary = summarize(states[order.id])
    return orders
