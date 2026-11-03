from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.orders.models import OrderModel
from .common import add_event, wire
from .inventory import paged
from .models import OrderEvent, PrintDocument, PrintJob, Receipt
from .printing import render_print, request_print

router = APIRouter(prefix="/order-operations", tags=["order operations"], dependencies=[Depends(require_current_user)])


class NotePayload(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=100)


class CorrectionPayload(NotePayload):
    reason: str = Field(min_length=1, max_length=1000)


class PrintPayload(BaseModel):
    order_ids: list[int] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=100)
    source_documents: dict[int, int] = Field(default_factory=dict)
    include_amount: bool = False
    picking_note: str = Field(default="", max_length=300)
    reprint_reason: str = Field(default="", max_length=1000)
    layout: Literal['orders', 'summary'] = 'orders'


@router.get("/orders/{order_id}/events")
def events(order_id: int, page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    result = paged(db, select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.sequence.desc()), page, 20)
    for event in result['items']:
        event.update(event_id=event['id'], changed_fields=event['details'].get('changed_fields', {}),
                     correlation_id=event['details'].get('correlation_id', event['idempotency_key']),
                     related_receipt_id=event['details'].get('receipt_id'), related_movement_ids=event['details'].get('movement_ids', []))
    return result


@router.get("/orders/{order_id}/receipts")
def receipts(order_id: int, db: Session = Depends(get_db)):
    return wire(list(db.scalars(select(Receipt).where(Receipt.order_id == order_id))))


@router.post("/orders/{order_id}/notes")
def append_note(order_id: int, payload: NotePayload, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    order = db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "订单不存在")
    content = payload.content.strip()
    if not content:
        raise HTTPException(422, "备注不能为空")
    key = f"note:{payload.idempotency_key}"
    previous = db.scalar(select(OrderEvent).where(OrderEvent.idempotency_key == key).with_for_update())
    if previous:
        if previous.order_id != order_id or previous.summary != content:
            raise HTTPException(409, "幂等键已用于其他备注")
        return wire(previous)
    event = add_event(db, order, "note_added", content, actor, key=key)
    db.commit()
    return wire(event)


@router.post('/orders/{order_id}/events/{event_id}/corrections')
def correct_event(order_id: int, event_id: int, payload: CorrectionPayload, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    order = db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
    original = db.get(OrderEvent, event_id)
    if order is None or original is None or original.order_id != order_id:
        raise HTTPException(404, '订单或原事件不存在')
    content, reason = payload.content.strip(), payload.reason.strip()
    if not content or not reason:
        raise HTTPException(422, '更正内容和更正原因均为必填')
    key = f'correction:{payload.idempotency_key}'
    previous = db.scalar(select(OrderEvent).where(OrderEvent.idempotency_key == key).with_for_update())
    if previous:
        if previous.order_id != order_id or previous.summary != content or previous.reason != reason or previous.details.get('corrects_event_id') != event_id:
            raise HTTPException(409, '幂等键已用于其他更正')
        return wire(previous)
    event = add_event(db, order, 'correction_added', content, actor, key=key, reason=reason,
                      details={'corrects_event_id': event_id, 'corrects_sequence': original.sequence,
                               'changed_fields': {'annotation': {'before': original.summary, 'after': content}}})
    db.commit()
    return wire(event)


@router.get("/orders/{order_id}/documents")
def documents(order_id: int, page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    return paged(db, select(PrintDocument).where(PrintDocument.order_id == order_id).order_by(PrintDocument.attempt_no.desc()), page, 20)


@router.post("/print-jobs", status_code=201)
def create_print_job(payload: PrintPayload, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    try:
        job = request_print(db, payload, actor)
    except IntegrityError:
        db.rollback()
        from .common import digest
        job = db.scalar(select(PrintJob).where(PrintJob.idempotency_key == payload.idempotency_key))
        if job is None or job.payload_hash != digest(payload.model_dump(exclude={"idempotency_key"})):
            raise HTTPException(409, "打印请求冲突，请刷新后重试")
        # Replays still pass the same live-state check as the ordinary request path.
        job = request_print(db, payload, actor)
    return {**wire(job), "documents": wire(list(db.scalars(select(PrintDocument).where(PrintDocument.job_id == job.id))))}


@router.get("/print-jobs/{job_id}/html", response_class=HTMLResponse)
def print_html(job_id: int, request: Request, document_id: int | None = None,
               layout: Literal['orders', 'summary'] = 'orders', db: Session = Depends(get_db)):
    job = db.get(PrintJob, job_id)
    if job is None:
        raise HTTPException(404, "打印任务不存在")
    if document_id is not None:
        document = db.get(PrintDocument, document_id)
        if document is None or document.job_id != job_id:
            raise HTTPException(404, "此任务不存在该文档")
    return HTMLResponse(render_print(db, job, document_id, layout=layout,
                                    api_base=str(request.base_url).rstrip('/') + '/api/order-operations'),
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get('/print-jobs/{job_id}/validate')
def validate_print(job_id: int, document_id: int | None = None, db: Session = Depends(get_db)):
    from app.features.orders.models import OrderStatus
    if db.get(PrintJob, job_id) is None:
        raise HTTPException(404, '打印任务不存在')
    ids = select(PrintDocument.order_id).where(PrintDocument.job_id == job_id)
    if document_id is not None:
        ids = ids.where(PrintDocument.id == document_id)
    orders = list(db.scalars(select(OrderModel).where(OrderModel.id.in_(ids))))
    if not orders:
        raise HTTPException(404, '打印文档或关联订单不存在')
    conflicts = [order.order_number for order in orders if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED)]
    if conflicts:
        raise HTTPException(409, '单据已失效，不能打印：' + '、'.join(conflicts))
    return {'orders': [{'id': order.id, 'status': order.status.value} for order in orders]}
