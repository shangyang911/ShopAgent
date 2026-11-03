"""快递工作台的履约归属兼容历史退款标记，退款不会抹掉实物交接事实。"""
from sqlalchemy import and_, case, or_

from app.features.operations.models import OrderFacts
from app.features.orders.models import OrderModel, OrderStatus
from .models import ShipmentModel


def workbench_scope():
    # 历史已退款且实际交接的包裹仍需物流跟踪；未出库退款不进入发货队列。
    legacy_dispatched = and_(OrderModel.status == OrderStatus.REFUNDED,
                            ShipmentModel.order_id.is_not(None),
                            or_(ShipmentModel.dispatched_at.is_not(None), ShipmentModel.historical == True))
    eligible = or_(OrderModel.status.in_((OrderStatus.PAID_PENDING_SHIPMENT, OrderStatus.IN_DELIVERY, OrderStatus.COMPLETED)), legacy_dispatched)
    stage = case((and_(legacy_dispatched, OrderFacts.completed_at.is_not(None)), "completed"),
                 (legacy_dispatched, "in_delivery"), else_=OrderModel.status)
    return eligible, stage
