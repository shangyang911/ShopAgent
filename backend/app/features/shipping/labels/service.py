from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.operations.common import add_event, digest, wire
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.fulfillment.service import save_shipment
from app.features.shipping.labels.models import LabelTask
from app.features.shipping.labels.provider import LabelProvider, LabelProviderError, LabelSettings, safe_label_url
from app.features.shipping.parcels.schemas import ParcelWrite
from app.features.shipping.parcels.service import assert_no_label_pending, carrier_identity, lock_order, parcel_rows, write_extra


def finish_task(db, task, result, actor):
    """把平台取号结果和包裹登记放入同一事务；提交动作留给上层统一完成。"""
    payload = ParcelWrite(carrier_name=task.carrier_name, tracking_number=result["tracking_number"],
                          remark="电子面单取号", expected_revision=task.expected_revision)
    if task.package_no == 1:
        save_shipment(db, task.order_id, payload, actor, label_task=task.id, commit=False)
    else:
        write_extra(db, task.order_id, task.package_no, payload, actor, label_task=task.id, commit=False)
    task.status, task.error = "issued", None
    task.tracking_number, task.label_url = result["tracking_number"], result["label_url"]
    task.provider_task_id = result.get("provider_task_id")
    order = db.get(OrderModel, task.order_id)
    add_event(db, order, "label_issued", f"包裹 {task.package_no} 电子面单已取号", actor,
              key=f"label-issued:{task.id}", details={"task_id": task.id, "package_no": task.package_no, "tracking_number": task.tracking_number})


async def create_label(db, order_id, package_no, payload, actor, settings=None):
    """取号流程：校验并保存任务 → 请求平台 → 登记返回结果；同键重试不会再次取号。"""
    order = lock_order(db, order_id)
    hashed = digest({"order_id": order_id, "package_no": package_no, **payload.model_dump(exclude={"idempotency_key"})})
    previous = db.scalar(select(LabelTask).where(LabelTask.idempotency_key == payload.idempotency_key))
    if previous:
        if previous.request_hash != hashed:
            raise HTTPException(409, "同一面单提交键不能用于不同内容")
        return wire(previous)
    assert_no_label_pending(db, order_id)
    first, rows = parcel_rows(db, order_id)
    if order.status != OrderStatus.PAID_PENDING_SHIPMENT or payload.expected_revision != (first.revision if first else 0):
        raise HTTPException(409, "订单状态或包裹版本已变化，请刷新后核对")
    if package_no in {no for no, _ in rows}:
        raise HTTPException(409, "该包裹已有运单，请直接使用原面单；不能重复取号")
    if package_no > 1 and not first:
        raise HTTPException(409, "请先创建一号包裹")
    settings = settings or LabelSettings.from_env()
    code = carrier_identity(payload.carrier_name)
    settings.account(code)  # 配置缺失不创建未决任务，也不调用平台。
    task = LabelTask(order_id=order_id, package_no=package_no, idempotency_key=payload.idempotency_key,
                     request_hash=hashed, carrier_name=payload.carrier_name, expected_revision=payload.expected_revision,
                     status="requesting", created_by=actor.username)
    db.add(task)
    add_event(db, order, "label_requested", f"申请包裹 {package_no} 电子面单", actor,
              key=f"label-request:{uuid4().hex}", details={"package_no": package_no})
    try:
        db.commit()  # 外部调用前持久化任务并释放锁；其余写操作会检查该任务。
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "面单任务已存在，请刷新核对") from error
    task_id = task.id
    try:
        result = await LabelProvider(settings).create(code, order, payload.weight, f"label-{task_id}-{digest(payload.idempotency_key)[:24]}")
    except LabelProviderError as error:
        # 请求超时时，平台可能已经取号成功。uncertain 的任务必须人工核实，不能盲目重新取号。
        lock_order(db, order_id)
        task = db.get(LabelTask, task_id, populate_existing=True)
        if task.status == "requesting":
            task.status, task.error = ("unknown" if error.uncertain else "failed"), str(error)
            db.commit()
        return wire(task)
    lock_order(db, order_id)
    task = db.get(LabelTask, task_id, populate_existing=True)
    if task.status != "requesting":
        return wire(task)
    try:
        finish_task(db, task, result, actor)
        db.commit()
    except (HTTPException, IntegrityError):
        db.rollback()
        lock_order(db, order_id)
        task = db.get(LabelTask, task_id, populate_existing=True)
        task.status, task.error = "unknown", "平台已返回取号结果，但本地登记冲突，请核实后补录"
        task.tracking_number, task.label_url = result["tracking_number"], result["label_url"]
        task.provider_task_id = result["provider_task_id"]
        db.commit()
    return wire(task)


def resolve_label(db, order_id, task_id, payload, actor):
    """人工核实不确定的面单结果：补登记已取到的号码，或记录确认未取号的依据。"""
    order = lock_order(db, order_id)
    task = db.get(LabelTask, task_id, populate_existing=True)
    if task is None or task.order_id != order_id:
        raise HTTPException(404, "面单任务不存在")
    if task.status not in {"requesting", "unknown"}:
        raise HTTPException(409, "该面单任务已处理，请刷新核对")
    # 防止操作员在仍在执行的网络请求中提前解除保护。
    from app.features.operations.models import utcnow
    if task.status == "requesting" and (utcnow() - task.created_at).total_seconds() < 120:
        raise HTTPException(409, "面单仍在申请，请稍后核实")
    if payload.outcome == "issued":
        url = safe_label_url(payload.label_url)
        if not url:
            raise HTTPException(422, "请填写快递100的有效面单链接")
        finish_task(db, task, {"tracking_number": payload.tracking_number, "label_url": url}, actor)
    else:
        task.status, task.error = "failed", "管理员已核实平台未取号：" + payload.reason
    add_event(db, order, "label_reconciled", "核实电子面单取号结果", actor,
              key=f"label-resolve:{task.id}", reason=payload.reason, details={"task_id": task.id, "outcome": payload.outcome})
    db.commit()
    return wire(task)
