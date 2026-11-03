"""物流同步分为领取任务、外部查询、核对后落库，网络等待期间不持有订单锁。"""
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.features.operations.common import wire
from app.features.operations.models import utcnow
from app.features.orders.models import OrderModel
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.tracking.models import ShipmentTrackingModel
from app.features.shipping.tracking.provider import Kuaidi100Provider, TrackingProviderError, carrier_code
from app.features.shipping.tracking.settings import TrackingSettings
from .context import models_for, package_filter, package_keys, shipment_for


STATUS_NAMES = {"unknown": "暂无可确认轨迹", "collected": "已揽收", "in_transit": "运输中",
                "out_for_delivery": "派件中", "signed": "平台显示已签收", "exception": "物流异常",
                "returning": "退回 / 退签中", "customs": "清关中"}


def tracking_snapshot(db, order_id, settings=None, *, package_no=1):
    """读取数据库中的物流快照；网页刷新和客服查物流都走这里，不调用外部平台。"""
    TrackingModel, EventModel = models_for(package_no)
    settings = settings or TrackingSettings.from_env()
    order = db.get(OrderModel, order_id)
    if order is None:
        raise HTTPException(404, "订单不存在")
    shipment = shipment_for(db, order_id, package_no)
    row = db.get(TrackingModel, order_id if package_no == 1 else (order_id, package_no))
    now = utcnow()
    eligible = bool(shipment and (shipment.dispatched_at or shipment.historical))
    # 节点时间与同步时间是两回事：前者是快递事件发生时间，后者是本系统取得数据的时间。
    # 有效时间倒序显示；时间未知的节点放在末尾，不能用入库时间假装其发生时间。
    events = list(db.scalars(select(EventModel).where(package_filter(EventModel, order_id, package_no))
        .order_by(EventModel.occurred_at.is_(None), EventModel.occurred_at.desc(), EventModel.id.desc()).limit(500)))
    total = db.scalar(select(func.count()).select_from(EventModel).where(package_filter(EventModel, order_id, package_no))) or 0
    return wire({"package_no": package_no, "order_id": order_id, "order_number": order.order_number,
        "carrier_name": shipment.carrier_name if shipment else None, "tracking_number": shipment.tracking_number if shipment else None,
        "service": settings.public(), "eligible": eligible, "status": row.status if row else "unknown",
        "status_name": STATUS_NAMES.get(row.status if row else "unknown", "暂无可确认轨迹"),
        "last_success_at": row.last_success_at if row else None, "last_attempt_at": row.last_attempt_at if row else None,
        "latest_event_at": row.latest_event_at if row else None, "latest_description": row.latest_description if row else None,
        "next_sync_at": row.next_sync_at if row else None, "error_code": row.error_code if row else None,
        "error_message": row.error_message if row else None,
        "syncing": bool(row and row.lease_until and row.lease_until > now),
        "stale": bool(row and row.last_success_at and (row.error_code or row.last_success_at < now - timedelta(hours=2))),
        "events": events, "event_total": total, "events_truncated": total > len(events),
        "estimated_delivery_at": None,
        "notice": "物流服务尚未配置，暂无可查询的实时轨迹" if not settings.configured else
                  "平台签收与订单人工完成分别记录；不根据高德驾车时间推测快递送达时间"})


async def sync_tracking(db, order_id, settings=None, *, package_no=1):
    """同步入口：资格与限频检查 → 领取任务并释放锁 → 查询平台 → 校验任务并提交结果。"""
    TrackingModel, EventModel = models_for(package_no)
    settings = settings or TrackingSettings.from_env()
    if not settings.configured:
        raise HTTPException(409, "物流查询服务尚未配置，请联系管理员配置快递100账号后同步")
    # 阶段一：按订单、运单、物流快照的顺序加锁，并刷新 ORM 缓存，读取并发操作后的真实值。
    order = db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "订单不存在")
    shipment = shipment_for(db, order_id, package_no, lock=True)
    if not shipment or not (shipment.dispatched_at or shipment.historical):
        raise HTTPException(409, "请先登记实际交接出库，尚未交接的运单不查询物流")
    now = utcnow()
    row = db.scalar(select(TrackingModel).where(package_filter(TrackingModel, order_id, package_no)).with_for_update().execution_options(populate_existing=True))
    # lease_until 表示其他请求正在查询；next_sync_at 表示仍在冷却期。两种情况均返回缓存。
    if row and ((row.lease_until and row.lease_until > now) or (row.next_sync_at and row.next_sync_at > now)):
        db.commit()
        return {**tracking_snapshot(db, order_id, settings, package_no=package_no), "sync_result": "cached"}
    revision, number = shipment.revision, shipment.tracking_number
    # 保存查询前的版本与单号，网络等待结束后用它们判断结果是否仍属于当前运单。
    phone = order.recipient_phone
    destination = f"{order.province}{order.city}{order.district}"
    validation_error = None
    try:
        code = carrier_code(shipment.carrier_name, settings)
    except TrackingProviderError as error:
        code, validation_error = "", error
    identity = sha256(f"{code}:{number.upper()}".encode()).hexdigest() if code else None
    # 规范编码统一“顺丰”和“顺丰速运”等别名，再交给数据库唯一约束阻止同单重复同步。
    if row is None:
        row = TrackingModel(**package_keys(order_id, package_no), status="unknown", shipment_revision=revision)
        db.add(row)
    row.identity_hash = identity
    row.last_attempt_at, row.next_sync_at = now, now + timedelta(minutes=settings.interval_minutes)
    # 查询尝试与成功查询分别记时；失败也保留查询间隔，防止反复点击连续访问外部平台。
    token = str(uuid4())
    row.lease_token, row.lease_until = token, now + timedelta(seconds=60)
    try:
        db.commit()  # 在访问外部平台之前释放锁，同时持久化查询间隔。
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "该实际运单已由其他订单同步，请核对快递公司别名和运单归属") from error
    result, failure = None, validation_error
    # 阶段二：此时已提交领取任务的事务；慢网络不会一直占用订单行锁。
    if failure is None:
        try:
            result = await Kuaidi100Provider(settings).query(code, number, phone, destination)
        except TrackingProviderError as error:
            failure = error
    # 阶段三：重新加锁；只有仍持有本次令牌、且运单版本未变化的请求可以落库。
    order = db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
    current = shipment_for(db, order_id, package_no, lock=True)
    row = db.scalar(select(TrackingModel).where(package_filter(TrackingModel, order_id, package_no)).with_for_update().execution_options(populate_existing=True))
    if row.lease_token != token or current.revision != revision or current.tracking_number != number:
        db.rollback()
        raise HTTPException(409, "运单信息或同步任务已变化，过期结果未写入")
    row.lease_token, row.lease_until = None, None
    if failure:
        # 只更新错误信息，保留旧节点和成功时间，让前端能展示“上次记录，当前待核实”。
        row.error_code, row.error_message = failure.code, str(failure)
    else:
        # 平台可能重复返回全部历史节点；按节点摘要去重，而不是每次同步都追加一份。
        known = set(db.scalars(select(EventModel.event_hash).where(package_filter(EventModel, order_id, package_no))))
        for event in result.events:
            if event["event_hash"] not in known:
                db.add(EventModel(**package_keys(order_id, package_no), **event))
        latest = result.events[0] if result.events else None
        latest_time = latest["occurred_at"] if latest else None
        newer = ((row.latest_event_at is None and (latest is not None or not known))
                 or (latest_time is not None and row.latest_event_at is not None and latest_time >= row.latest_event_at))
        # 旧查询结果可补齐历史节点，但不能让最新进度或签收状态倒退。
        if newer and not (row.status == "signed" and result.status != "signed" and latest_time == row.latest_event_at):
            row.status, row.provider_state = result.status, result.provider_state
            row.latest_event_at = latest_time
            row.latest_description = latest["description"] if latest else None
        row.last_success_at, row.error_code, row.error_message = utcnow(), None, None
    # 仅保存物流轨迹，不修改库存或订单完成状态。
    db.commit()
    return {**tracking_snapshot(db, order_id, settings, package_no=package_no), "sync_result": "failed" if failure else "updated"}


async def poll_due_shipments(factory, settings=None):
    """后台批量同步：只领取到期的未签收运单，每单使用独立 Session，避免一单冲突影响整批。"""
    settings = settings or TrackingSettings.from_env()
    if not (settings.configured and settings.auto_sync):
        return 0
    with factory() as db:
        # 尚未查询过的运单排前面；每轮最多20单，避免一次维护长时间占用处理资源。
        ids = list(db.scalars(select(ShipmentModel.order_id).outerjoin(ShipmentTrackingModel,
            ShipmentTrackingModel.order_id == ShipmentModel.order_id).where(
            or_(ShipmentModel.dispatched_at.is_not(None), ShipmentModel.historical == True),
            or_(ShipmentTrackingModel.status.is_(None), ShipmentTrackingModel.status != "signed"),
            or_(ShipmentTrackingModel.next_sync_at.is_(None), ShipmentTrackingModel.next_sync_at <= utcnow())
        ).order_by(ShipmentTrackingModel.last_attempt_at.is_not(None), ShipmentTrackingModel.last_attempt_at, ShipmentModel.order_id).limit(20)))
    for order_id in ids:
        with factory() as db:
            try:
                await sync_tracking(db, order_id, settings)
            except HTTPException:
                db.rollback()
    # 主包裹与附加包裹各自限频，整单首包裹签收后仍继续跟踪其他包裹。
    from .packages import poll_extra_packages
    return len(ids) + await poll_extra_packages(factory, settings)
