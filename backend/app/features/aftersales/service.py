"""售后写入先锁订单，再以当前读校验子记录；资金、验收和库存动作分别留痕。"""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.db.transactions import retry_deadlocks

from app.features.operations.common import add_event, digest, utc_input
from app.features.operations.inventory import move
from app.features.operations.models import Receipt, utcnow
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderItemModel, OrderStatus
from app.features.products.models import ProductModel
from app.features.shipping.parcels.service import assert_no_label_pending, lock_order
from .models import AfterSale, RefundRecord, ReturnLine
from .queries import case_detail, refunded_total


def locked_case(db, order_id, case_id, expected_revision=None):
    """订单锁协调退款、退货与发货；售后版本号阻止旧页面继续提交。"""
    order = lock_order(db, order_id)
    case = db.scalar(select(AfterSale).where(AfterSale.id == case_id, AfterSale.order_id == order_id).with_for_update().execution_options(populate_existing=True))
    if case is None:
        raise HTTPException(404, "售后单不存在")
    if expected_revision is not None and case.revision != expected_revision:
        raise HTTPException(409, "售后单已变化，请刷新核对")
    return order, case


def complete_if_ready(db, case):
    refunded = refunded_total(db, case.order_id, case_id=case.id, current=True)
    # 退款完成与验收完成都满足后才关闭退货售后；仅退款无需等待仓库。
    if refunded == case.requested_amount and (case.kind == "refund" or case.inspected_at):
        case.status = "closed"


@retry_deadlocks
def create_case(db, order_id, payload, actor):
    """创建申请只占用售后流程，不执行退款，也不恢复库存；失败由请求事务回滚。"""
    order = lock_order(db, order_id)
    hashed = digest({"order_id": order_id, **payload.model_dump()})
    previous = db.scalar(select(AfterSale).where(AfterSale.creation_key == payload.idempotency_key)
                         .with_for_update().execution_options(populate_existing=True))
    if previous:
        if previous.creation_hash != hashed:
            raise HTTPException(409, "提交键已用于其他售后内容")
        return case_detail(db, previous, current=True)
    receipt = db.scalar(select(Receipt).where(Receipt.order_id == order_id)
                        .with_for_update().execution_options(populate_existing=True))
    if receipt is None or order.status == OrderStatus.PENDING_PAYMENT:
        raise HTTPException(409, "尚无核实的收款记录，不能登记退款售后")
    # 简化当前工作流：同单只允许一个处理中售后，防止两张申请争用可退款余额。
    # 订单锁只保证串行执行；子表若普通读取，仍可能看不到等待锁期间已提交的售后。
    if db.scalar(select(AfterSale.id).where(AfterSale.order_id == order_id, AfterSale.status == "open").with_for_update()):
        raise HTTPException(409, "请先完成或撤销售后中的申请")
    if payload.requested_amount > receipt.amount - refunded_total(db, order_id, current=True):
        raise HTTPException(409, "申请金额超过剩余可退金额")
    if payload.kind == "return_refund" and not order.dispatch_started_at:
        raise HTTPException(409, "未出库订单无需退货，请使用仅退款")
    case = AfterSale(order_id=order_id, kind=payload.kind, requested_amount=payload.requested_amount,
                     reason=payload.reason, creation_key=payload.idempotency_key, creation_hash=hashed, created_by=actor.username)
    db.add(case)
    db.flush()
    for item in payload.items:
        line = db.scalar(select(OrderItemModel).where(OrderItemModel.id == item.order_item_id, OrderItemModel.order_id == order_id))
        if line is None:
            raise HTTPException(422, "退货商品不属于该订单")
        # 已完成的退货也占用原订单件数；只有撤销的申请不计入累计数量。
        previous_qty = sum(db.scalars(select(ReturnLine.quantity).join(AfterSale, AfterSale.id == ReturnLine.case_id).where(
            ReturnLine.order_item_id == line.id, AfterSale.status != "cancelled").order_by(ReturnLine.id).with_for_update()))
        if item.quantity + previous_qty > line.quantity:
            raise HTTPException(409, "累计申请退货数量超过原订单商品数量")
        db.add(ReturnLine(case_id=case.id, order_item_id=line.id, product_id=line.product_id, product_name=line.product_name, quantity=item.quantity))
    add_event(db, order, "aftersale_created", "创建售后申请；尚未执行退款或恢复库存", actor,
              key=f"aftersale:{case.id}:create", reason=payload.reason, details={"case_id": case.id, "amount": payload.requested_amount})
    db.commit()
    return case_detail(db, case)


@retry_deadlocks
def record_refund(db, order_id, case_id, payload, actor):
    """登记人工核实的退款凭据，并按最新余额校验；本方法不调用外部支付接口。"""
    # 先锁订单，再检查幂等记录；同单并发退款不能各自读取相同余额后重复退款。
    order, case = locked_case(db, order_id, case_id)
    hashed = digest({"order_id": order_id, "case_id": case_id, **payload.model_dump()})
    previous = db.scalar(select(RefundRecord).where(RefundRecord.idempotency_key == payload.idempotency_key)
                         .with_for_update().execution_options(populate_existing=True))
    if previous:
        if previous.request_hash != hashed:
            raise HTTPException(409, "退款提交键已用于其他内容")
        return case_detail(db, case, current=True)
    if case.status != "open" or case.revision != payload.expected_revision:
        raise HTTPException(409, "售后单已变化，请刷新核对")
    occurred_at = utc_input(payload.occurred_at)
    receipt = db.scalar(select(Receipt).where(Receipt.order_id == order_id)
                        .with_for_update().execution_options(populate_existing=True))
    # 旧 MySQL DATETIME 不含小数秒，写入可能四舍五入到下一秒。
    # 仅为旧收款事实放宽一秒存储精度，避免立即退款被误判为早于收款；未来时间仍严格拒绝。
    precision = timedelta(seconds=1) if db.bind.dialect.name == "mysql" else timedelta(0)
    if occurred_at > utcnow() or receipt is None or occurred_at < receipt.occurred_at - precision:
        raise HTTPException(422, "退款发生时间应介于收款时间与当前时间之间")
    total = refunded_total(db, order_id, current=True)
    case_total = refunded_total(db, order_id, case_id=case_id, current=True)
    if total + payload.amount > receipt.amount or case_total + payload.amount > case.requested_amount:
        raise HTTPException(409, "累计退款超过实际收款或本次申请金额")
    # 全额退款且未出库时，取消履约并释放预留；在途/完成订单保留履约状态。
    # update_status(commit=False) 将库存释放、退款凭据与事件合并为一次事务。
    if total + payload.amount == receipt.amount and order.status == OrderStatus.PAID_PENDING_SHIPMENT:
        assert_no_label_pending(db, order_id)
        _, error = OrderCRUD(db).update_status(order_id, OrderStatus.CANCELLED, actor, "售后全额退款，取消未出库履约", commit=False)
        if error:
            raise HTTPException(409, error)
    refund = RefundRecord(case_id=case_id, order_id=order_id, amount=payload.amount, reference=payload.reference,
                          occurred_at=occurred_at, recorded_by=actor.username, idempotency_key=payload.idempotency_key, request_hash=hashed)
    db.add(refund)
    db.flush()
    case.revision += 1
    complete_if_ready(db, case)
    add_event(db, order, "refund_recorded", "核实并登记实际退款（人工凭据）", actor,
              key=f"refund:{refund.id}", details={"case_id": case_id, "refund_id": refund.id, "amount": payload.amount, "reference": payload.reference}, occurred_at=occurred_at)
    db.commit()
    return case_detail(db, case)


def inspect_return(db, order_id, case_id, payload, actor):
    """完整验收每条退货明细；合格件按原 SKU 入库，损坏件仅记录，不增加可售。"""
    order, case = locked_case(db, order_id, case_id)
    if case.inspected_at:
        # 网络重试可返回已验收结果；不同数量或说明不能覆写既有验收。
        saved = list(db.scalars(select(ReturnLine).where(ReturnLine.case_id == case_id)
                               .with_for_update().execution_options(populate_existing=True)))
        expected = sorted((r.order_item_id, r.accepted_quantity, r.damaged_quantity) for r in saved)
        actual = sorted((r.order_item_id, r.accepted_quantity, r.damaged_quantity) for r in payload.items)
        if expected != actual or case.inspection_note != payload.note:
            raise HTTPException(409, "已验收记录只读，不能覆盖")
        return case_detail(db, case, current=True)
    if case.status != "open" or case.kind != "return_refund" or case.revision != payload.expected_revision:
        raise HTTPException(409, "当前售后不可验收，请刷新核对")
    lines = list(db.scalars(select(ReturnLine).where(ReturnLine.case_id == case_id)
                           .with_for_update().execution_options(populate_existing=True)))
    supplied = {r.order_item_id: r for r in payload.items}
    if len(supplied) != len(payload.items) or set(supplied) != {r.order_item_id for r in lines}:
        raise HTTPException(422, "请完整填写每一项退货商品的验收结果")
    # 所有数量先校验，再按商品 ID 加锁；保持与下单、补货相同的锁顺序。
    for line in lines:
        value = supplied[line.order_item_id]
        if value.accepted_quantity + value.damaged_quantity != line.quantity:
            raise HTTPException(422, "可售件数与损坏件数之和必须等于本次退货数量")
    products = {}
    for pid in sorted({r.product_id for r in lines if r.product_id is not None}):
        products[pid] = db.scalar(select(ProductModel).where(ProductModel.id == pid).with_for_update().execution_options(populate_existing=True))
    movement_ids = []
    for line in lines:
        value = supplied[line.order_item_id]
        if value.accepted_quantity:
            product = products.get(line.product_id)
            if product is None:
                raise HTTPException(409, "原商品已删除，不能将退件计入不存在的商品库存")
            original_item = db.get(OrderItemModel, line.order_item_id)
            movement = move(db, product, kind="return_received", available=value.accepted_quantity, reserved=0,
                            reference_type="aftersale", reference_id=case_id, reference_number=f"AS-{case_id}",
                            key=f"return:{line.id}", actor=actor, reason=payload.note,
                            sku_id=original_item.sku_id if original_item else None)
            movement_ids.append(movement.id)
        # 损坏件仅登记验收事实，禁止恢复可售库存。
        line.accepted_quantity, line.damaged_quantity = value.accepted_quantity, value.damaged_quantity
    case.inspected_at, case.inspected_by, case.inspection_note = utcnow(), actor.username, payload.note
    case.revision += 1
    complete_if_ready(db, case)
    add_event(db, order, "return_inspected", "退件验收完成，仅合格件恢复库存", actor,
              key=f"aftersale:{case_id}:inspect", reason=payload.note, details={"case_id": case_id, "movement_ids": movement_ids})
    db.commit()
    return case_detail(db, case)


def cancel_case(db, order_id, case_id, payload, actor):
    """仅撤销尚无退款和验收事实的申请，已经发生的资金或库存动作不可抹除。"""
    order, case = locked_case(db, order_id, case_id, payload.expected_revision)
    if case.status != "open" or case.inspected_at or db.scalar(select(RefundRecord.id).where(RefundRecord.case_id == case_id).with_for_update()):
        raise HTTPException(409, "已有退款或验收事实的售后不能撤销")
    case.status, case.revision = "cancelled", case.revision + 1
    add_event(db, order, "aftersale_cancelled", "撤销售后申请", actor, key=f"aftersale:{case_id}:cancel")
    db.commit()
    return case_detail(db, case)
