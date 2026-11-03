"""批量交接接口，沿用逐单事务和幂等保护。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.operations.common import digest
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.shipping.fulfillment.models import DispatchBatch
from app.features.shipping.fulfillment.schemas import BatchDispatch


router = APIRouter(prefix="/shipping", tags=["shipping"], dependencies=[Depends(require_current_user)])


@router.post("/dispatch-batch")
def batch_dispatch(payload: BatchDispatch, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    hashed = digest([item.model_dump() for item in sorted(payload.items, key=lambda item: item.order_id)])
    previous = db.get(DispatchBatch, payload.idempotency_key)
    if previous and previous.request_hash != hashed:
        raise HTTPException(409, "批量提交键已被用于其他订单或版本")
    if not previous:
        db.add(DispatchBatch(idempotency_key=payload.idempotency_key, request_hash=hashed, created_by=actor.username))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            previous = db.get(DispatchBatch, payload.idempotency_key)
            if not previous or previous.request_hash != hashed:
                raise HTTPException(409, "批量提交冲突，请刷新核对")
    results = []
    for item in sorted(payload.items, key=lambda item: item.order_id):
        try:
            row, error = OrderCRUD(db).update_status(item.order_id, OrderStatus.IN_DELIVERY, actor,
                idempotency_key=f"batch:{digest(payload.idempotency_key)[:32]}:{item.order_id}", shipment_revision=item.expected_revision)
            if error:
                db.rollback()
            results.append({"order_id": item.order_id, "success": error is None, "error": error})
        except (HTTPException, SQLAlchemyError) as error:
            db.rollback()
            results.append({"order_id": item.order_id, "success": False,
                            "error": error.detail if isinstance(error, HTTPException) else "保存失败，请沿用本次批次重试"})
    return {"items": results, "succeeded": sum(row["success"] for row in results), "failed": sum(not row["success"] for row in results)}
