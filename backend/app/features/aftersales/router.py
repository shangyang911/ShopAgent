"""订单售后接口仅做 HTTP 适配，事务规则集中在 service。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from .queries import order_snapshot
from .schemas import CaseCreate, CaseVersion, InspectionWrite, RefundWrite
from .service import cancel_case, create_case, inspect_return, record_refund

router = APIRouter(prefix="/orders/{order_id}/aftersales", tags=["aftersales"], dependencies=[Depends(require_current_user)])


@router.get("")
def snapshot(order_id: int, db: Session = Depends(get_db)):
    # 普通刷新只读，不会触发退款或退件入库。
    return order_snapshot(db, order_id)


@router.post("", status_code=201)
def create(order_id: int, payload: CaseCreate, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return create_case(db, order_id, payload, actor)


@router.post("/{case_id}/refunds")
def refund(order_id: int, case_id: int, payload: RefundWrite, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return record_refund(db, order_id, case_id, payload, actor)


@router.post("/{case_id}/inspection")
def inspect(order_id: int, case_id: int, payload: InspectionWrite, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return inspect_return(db, order_id, case_id, payload, actor)


@router.post("/{case_id}/cancel")
def cancel(order_id: int, case_id: int, payload: CaseVersion, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    # 撤销只允许尚未产生资金和实物事实的申请。
    return cancel_case(db, order_id, case_id, payload, actor)
