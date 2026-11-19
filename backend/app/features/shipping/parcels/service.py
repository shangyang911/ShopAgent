"""多包裹与结算运费；所有修改先锁订单，整单 revision 覆盖全部运单。"""
from decimal import Decimal
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.transactions import retry_deadlocks
from app.features.operations.common import add_event, wire
from app.features.operations.models import utcnow
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.labels.models import LabelTask
from app.features.shipping.parcels.models import ExtraParcel, FreightRecord, WaybillIdentity
from app.features.shipping.tracking.provider import CARRIER_CODES
from app.features.shipping.tracking.settings import TrackingSettings


def lock_order(db, order_id):
    order = db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "订单不存在")
    return order


def assert_no_label_pending(db, order_id, except_task=None):
    """取号结果未确认前暂停改运单；面单任务本身落库时可排除自己的任务 ID。"""
    statement = select(LabelTask.id).where(LabelTask.order_id == order_id, LabelTask.status.in_(("requesting", "unknown")))
    if except_task is not None:
        statement = statement.where(LabelTask.id != except_task)
    # 订单锁只串行化写入，普通读取仍可能沿用鉴权快照，必须当前读检查未决取号。
    if db.scalar(statement.limit(1).with_for_update()):
        raise HTTPException(409, "电子面单正在申请或结果待核实，请先核实面单任务")


def carrier_identity(name):
    name = name.strip()
    mappings = {**CARRIER_CODES, **TrackingSettings.from_env().carrier_codes}
    return mappings.get(name, name.strip().lower())


def claim_waybill(db, order_id, package_no, carrier, number):
    """唯一键阻止并发重复；同时检查升级前尚未建立索引的历史运单。"""
    code = carrier_identity(carrier)
    aliases = {name for name in {**CARRIER_CODES, **TrackingSettings.from_env().carrier_codes} if carrier_identity(name) == code}
    aliases.update((carrier, code))
    for model, no in ((ShipmentModel, 1), (ExtraParcel, None)):
        matches = db.scalars(select(model).where(func.lower(model.carrier_name).in_({name.lower() for name in aliases}), func.upper(model.tracking_number) == number.upper()))
        for row in matches:
            if (row.order_id, no or row.package_no) != (order_id, package_no):
                raise HTTPException(409, "该运单已关联其他订单或包裹")
    identity = sha256(f"{code}:{number.upper()}".encode()).hexdigest()
    previous = db.scalar(select(WaybillIdentity).where(WaybillIdentity.order_id == order_id, WaybillIdentity.package_no == package_no)
                         .with_for_update().execution_options(populate_existing=True))
    if previous and previous.identity == identity:
        return
    if previous:
        db.delete(previous)
        db.flush()
    db.add(WaybillIdentity(identity=identity, order_id=order_id, package_no=package_no))
    db.flush()


def parcel_rows(db, order_id, *, current=False):
    """统一主包裹和附加包裹的读取格式：一号仍沿用原运单表，其他包裹单独保存。"""
    first = db.get(ShipmentModel, order_id, with_for_update=current, populate_existing=current)
    rows = [(1, first)] if first else []
    extras = select(ExtraParcel).where(ExtraParcel.order_id == order_id).order_by(ExtraParcel.package_no)
    if current:
        extras = extras.with_for_update().execution_options(populate_existing=True)
    rows.extend((row.package_no, row) for row in db.scalars(extras))
    return first, rows


def package_snapshot(db, order_id, *, current=False):
    """将包裹、运费和最新面单任务合并成页面快照；金额用 Decimal 汇总。"""
    order = db.get(OrderModel, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    first, rows = parcel_rows(db, order_id, current=current)
    costs_query = select(FreightRecord).where(FreightRecord.order_id == order_id)
    tasks_query = select(LabelTask).where(LabelTask.order_id == order_id).order_by(LabelTask.id.desc())
    if current:
        costs_query = costs_query.with_for_update().execution_options(populate_existing=True)
        tasks_query = tasks_query.with_for_update().execution_options(populate_existing=True)
    costs = {row.package_no: row for row in db.scalars(costs_query)}
    tasks = list(db.scalars(tasks_query))
    labels = {}
    for task in tasks:
        labels.setdefault(task.package_no, task)
    items = []
    for no, row in rows:
        cost, label = costs.get(no), labels.get(no)
        items.append({"package_no": no, "carrier_name": row.carrier_name, "tracking_number": row.tracking_number,
                      "remark": row.remark, "freight": wire(cost) if cost else None,
                      "label": wire(label) if label else None})
    return {"order_id": order_id, "aftersale_pending": order.aftersale_pending,
            "revision": first.revision if first else 0, "items": items,
            "actual_cost": wire(sum((cost.actual_cost for cost in costs.values()), start=Decimal('0.00'))),
            "unsettled_packages": len(rows) - len(costs), "label_tasks": wire(tasks)}


@retry_deadlocks
def write_extra(db, order_id, package_no, payload, actor, *, label_task=None, commit=True):
    """交接前登记附加包裹；同时提升主运单版本，使旧的整单交接预览立即失效。"""
    actor_name = actor.username
    order = lock_order(db, order_id)
    assert_no_label_pending(db, order_id, label_task)
    first = db.get(ShipmentModel, order_id, with_for_update=True, populate_existing=True)
    if order.status != OrderStatus.PAID_PENDING_SHIPMENT or not first or first.dispatched_at or first.historical:
        raise HTTPException(409, "请先登记一号包裹；仅待发货订单可增改包裹")
    row = db.get(ExtraParcel, (order_id, package_no), with_for_update=True, populate_existing=True)
    values = {key: getattr(payload, key) for key in ("carrier_name", "tracking_number", "remark")}
    if row and all(getattr(row, key) == value for key, value in values.items()):
        return package_snapshot(db, order_id, current=True)
    if first.revision != payload.expected_revision:
        raise HTTPException(409, "包裹清单已变化，请刷新后核对")
    if db.scalar(select(LabelTask.id).where(LabelTask.order_id == order_id, LabelTask.package_no == package_no, LabelTask.status == "issued").with_for_update()):
        raise HTTPException(409, "已取得电子面单的包裹不能覆盖运单")
    try:
        claim_waybill(db, order_id, package_no, payload.carrier_name, payload.tracking_number)
        if row is None:
            row = ExtraParcel(order_id=order_id, package_no=package_no, **values)
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        row.updated_by, row.updated_at = actor_name, utcnow()
        first.revision += 1
        first.updated_by, first.updated_at = actor_name, row.updated_at
        add_event(db, order, "package_recorded", f"登记包裹 {package_no}（尚未交接）", actor,
                  key=f"packages:{order_id}:{first.revision}", details={"package_no": package_no, "revision": first.revision, **values})
        if commit:
            db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "该运单已关联其他包裹，请刷新核对") from error
    return package_snapshot(db, order_id, current=not commit)


@retry_deadlocks
def delete_extra(db, order_id, package_no, revision, actor):
    """只移除尚未交接且没有面单记录的附加包裹，保留可追溯的订单事件。"""
    actor_name = actor.username
    order = lock_order(db, order_id)
    assert_no_label_pending(db, order_id)
    first = db.get(ShipmentModel, order_id, with_for_update=True, populate_existing=True)
    row = db.get(ExtraParcel, (order_id, package_no), with_for_update=True, populate_existing=True)
    if not row:
        raise HTTPException(404, "包裹不存在")
    if order.status != OrderStatus.PAID_PENDING_SHIPMENT or not first or first.revision != revision:
        raise HTTPException(409, "订单已交接或包裹清单已更新")
    if db.scalar(select(LabelTask.id).where(LabelTask.order_id == order_id, LabelTask.package_no == package_no).with_for_update()):
        raise HTTPException(409, "存在电子面单记录的包裹需保留以便核查")
    identity = db.scalar(select(WaybillIdentity).where(WaybillIdentity.order_id == order_id, WaybillIdentity.package_no == package_no)
                        .with_for_update().execution_options(populate_existing=True))
    if identity:
        db.delete(identity)
    db.delete(row)
    first.revision += 1
    first.updated_by, first.updated_at = actor_name, utcnow()
    add_event(db, order, "package_removed", f"移除未发货包裹 {package_no}", actor,
              key=f"packages:{order_id}:{first.revision}", details={"package_no": package_no})
    db.commit()
    return package_snapshot(db, order_id)


@retry_deadlocks
def write_freight(db, order_id, package_no, payload, actor):
    """交接后按包裹登记实际支出；与订单里向客户收取的 shipping_fee 分开记录。"""
    actor_name = actor.username
    order = lock_order(db, order_id)
    first, rows = parcel_rows(db, order_id, current=True)
    if package_no not in {no for no, _ in rows}:
        raise HTTPException(404, "包裹不存在")
    if not first or not (first.dispatched_at or first.historical):
        raise HTTPException(409, "包裹交接后才能登记实际运费")
    row = db.get(FreightRecord, (order_id, package_no), with_for_update=True, populate_existing=True)
    if row and row.actual_cost == payload.actual_cost and row.source == payload.source:
        return package_snapshot(db, order_id, current=True)
    if payload.expected_revision != (row.revision if row else 0):
        raise HTTPException(409, "运费已被更新，请刷新后核对")
    before = wire(row) if row else None
    if row is None:
        row = FreightRecord(order_id=order_id, package_no=package_no, revision=1)
        db.add(row)
    else:
        row.revision += 1
    row.actual_cost, row.source = payload.actual_cost, payload.source
    row.updated_by, row.updated_at = actor_name, utcnow()
    add_event(db, order, "freight_settled", f"登记包裹 {package_no} 实际运费", actor,
              key=f"freight:{order_id}:{package_no}:{row.revision}", details={"before": before, "after": wire(row)})
    db.commit()
    return package_snapshot(db, order_id)
