"""快递履约：保存运单不改库存，实物交接复用订单事务，历史补录不重复出库。"""
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.transactions import retry_deadlocks
from app.features.operations.common import add_event
from app.features.operations.models import OrderFacts, utcnow
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.fulfillment.models import ShipmentModel
from .queries import workbench_scope
from app.features.shipping.tracking.summary import attach_summaries


WORKBENCH_STATUSES = (OrderStatus.PAID_PENDING_SHIPMENT, OrderStatus.IN_DELIVERY, OrderStatus.COMPLETED)


def workbench(db, *, status=None, keyword="", page=1, page_size=20):
    """组装工作台列表；在数据库筛选分页，并将统计和商品明细一起返回给页面。"""
    eligible, stage = workbench_scope()
    statement = select(OrderModel).outerjoin(OrderFacts, OrderFacts.order_id == OrderModel.id).outerjoin(ShipmentModel, ShipmentModel.order_id == OrderModel.id).where(eligible)
    if keyword.strip():
        from app.features.shipping.parcels.models import ExtraParcel
        text = keyword.strip()
        statement = statement.where(or_(OrderModel.order_number.contains(text, autoescape=True),
            OrderModel.recipient_name.contains(text, autoescape=True), OrderModel.recipient_phone.contains(text, autoescape=True),
            ShipmentModel.tracking_number.contains(text, autoescape=True),
            OrderModel.id.in_(select(ExtraParcel.order_id).where(ExtraParcel.tracking_number.contains(text, autoescape=True)))))
    # 汇总保留搜索条件，但不受当前标签和分页影响。
    counts = dict(db.execute(statement.with_only_columns(stage, func.count()).group_by(stage)).all())
    if status:
        statement = statement.where(stage == status)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.scalars(statement.options(selectinload(OrderModel.items), selectinload(OrderModel.facts)).order_by(
        OrderFacts.paid_at.is_(None), OrderFacts.paid_at.asc(), OrderModel.id.asc()
    ).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": attach_summaries(db, list(rows)), "total": total, "page": page, "page_size": page_size,
            "counts": {getattr(state, "value", state): count for state, count in counts.items()}}


@retry_deadlocks
def save_shipment(db, order_id, payload, actor, *, label_task=None, commit=True):
    """登记主包裹运单，不执行出库；面单服务可用 commit=False 将登记并入它自己的事务。"""
    # 先读取可能过期的 ORM 账号属性，避免在新运单字段尚未填完时触发 autoflush。
    actor_name, recorded_at = actor.username, utcnow()
    # 与订单取消/出库锁同一订单行，防止运单编辑与业务状态变化交错提交。
    order = db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "订单不存在")
    if order.status not in WORKBENCH_STATUSES:
        raise HTTPException(409, "仅待发货、已发货和已完成订单可以登记快递信息")
    from app.features.shipping.parcels.service import assert_no_label_pending, claim_waybill
    from app.features.shipping.labels.models import LabelTask
    assert_no_label_pending(db, order_id, label_task)
    # 当前读取得最新记录并刷新 ORM；普通查询仍可能沿用原事务快照。
    # 缺失行的间隙锁死锁由外层重试整笔数据库事务。
    row = db.get(ShipmentModel, order_id, with_for_update=True, populate_existing=True)
    values = {key: getattr(payload, key) for key in ("carrier_name", "tracking_number", "remark")}
    if row and all(getattr(row, key) == value for key, value in values.items()):
        return row  # 保存成功但响应丢失时，原内容重试不会追加第二条事件。
    if row and (order.status != OrderStatus.PAID_PENDING_SHIPMENT or row.dispatched_at is not None or row.historical):
        raise HTTPException(409, "已交接或历史补录的快递信息只读，不能直接覆盖")
    if payload.expected_revision != (row.revision if row else 0):
        raise HTTPException(409, "快递信息已被其他操作更新，请刷新后核对")
    if db.scalar(select(LabelTask.id).where(LabelTask.order_id == order_id, LabelTask.package_no == 1, LabelTask.status == "issued").with_for_update()):
        raise HTTPException(409, "已取得电子面单的运单不能直接覆盖")
    historical = order.status in (OrderStatus.IN_DELIVERY, OrderStatus.COMPLETED)
    if historical and not payload.remark:
        raise HTTPException(422, "历史补录请在备注说明信息来源；不会重新出库")
    before = {key: getattr(row, key) for key in values} if row else None
    if row is None:
        row = ShipmentModel(order_id=order_id, revision=1, historical=historical,
                            updated_by=actor_name, updated_at=recorded_at, **values)
        db.add(row)
        if historical:
            facts = db.get(OrderFacts, order_id)
            row.dispatched_at = facts.dispatch_started_at if facts else None
    else:
        row.revision += 1
        for key, value in values.items():
            setattr(row, key, value)
    row.updated_by, row.updated_at = actor_name, recorded_at
    try:
        claim_waybill(db, order_id, 1, payload.carrier_name, payload.tracking_number)
        add_event(db, order, "shipment_recorded", "补录历史快递信息（不重复出库）" if historical else "登记快递信息（尚未交接出库）",
            actor, key=f"shipment:{order_id}:{row.revision}", details={"shipment_revision": row.revision,
            "changed_fields": {key: {"before": before[key] if before else None, "after": value} for key, value in values.items()},
            "historical": historical})
        if commit:
            db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "该快递公司和运单号已关联其他订单，请核对后重试") from error
    return row
