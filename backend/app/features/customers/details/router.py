"""客户详情接口沿用管理员登录态，历史订单仅提供查询。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.features.auth.router import require_current_user
from .schemas import ProfileWrite
from . import service

router = APIRouter(prefix="/customers", tags=["customer-details"], dependencies=[Depends(require_current_user)])


@router.get("/{customer_id}")
def detail(customer_id: int, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    return service.detail(db, customer_id, page, page_size)


@router.put("/{customer_id}/profile")
def profile(customer_id: int, payload: ProfileWrite, db: Session = Depends(get_db)):
    return service.save_profile(db, customer_id, payload)
