"""面单打印意图审计；仅返回已取号的官方图片链接，不把打开页面视为打印成功。"""
from fastapi import HTTPException
from sqlalchemy import select

from app.features.operations.common import add_event, digest
from app.features.operations.models import OrderEvent
from app.features.orders.models import OrderStatus
from app.features.shipping.labels.models import LabelTask
from app.features.shipping.labels.provider import safe_label_url
from app.features.shipping.parcels.service import assert_no_label_pending, lock_order, parcel_rows


def print_labels(db, payload, actor):
    signature = digest([item.model_dump() for item in sorted(payload.items, key=lambda item: item.order_id)])
    labels, orders = [], []
    for item in sorted(payload.items, key=lambda item: item.order_id):
        order = lock_order(db, item.order_id)
        assert_no_label_pending(db, order.id)
        first, rows = parcel_rows(db, order.id)
        if order.status not in {OrderStatus.PAID_PENDING_SHIPMENT, OrderStatus.IN_DELIVERY, OrderStatus.COMPLETED} or not first or first.revision != item.expected_revision:
            raise HTTPException(409, "订单状态或包裹版本已变化，不能打印该批次")
        tasks = {row.package_no: row for row in db.scalars(select(LabelTask).where(LabelTask.order_id == order.id, LabelTask.status == 'issued'))}
        for no, row in rows:
            task = tasks.get(no)
            if not task or task.tracking_number != row.tracking_number or not safe_label_url(task.label_url):
                raise HTTPException(409, f"订单 {order.order_number} 包裹 {no} 没有可用电子面单；请单独打印已有纸质运单")
            labels.append({"order_id": order.id, "order_number": order.order_number, "package_no": no,
                           "tracking_number": row.tracking_number, "label_url": safe_label_url(task.label_url)})
        key = f"label-print:{digest(payload.idempotency_key)[:32]}:{order.id}"
        previous = db.scalar(select(OrderEvent).where(OrderEvent.idempotency_key == key))
        if previous and previous.details.get('request_hash') != signature:
            raise HTTPException(409, "打印提交键已用于其他批次")
        orders.append((order, key, previous))
    for order, key, previous in orders:
        if not previous:
            add_event(db, order, "label_print_requested", "申请打开电子面单打印页（非打印成功回执）", actor,
                      key=key, details={"request_hash": signature, "package_count": sum(row['order_id'] == order.id for row in labels)})
    db.commit()
    return {"items": labels}
