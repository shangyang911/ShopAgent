"""批量商品入口；预览只读，确认才提交业务事务。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from .schemas import BatchApply, BatchPreview
from . import service

router = APIRouter(prefix="/products/batch", tags=["product-batch"])


@router.post("/preview")
def preview(payload: BatchPreview, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    return service.snapshot(db, actor, payload)[2]


@router.post("/apply")
def apply(payload: BatchApply, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    return service.apply(db, actor, payload)
