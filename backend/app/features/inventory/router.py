"""库存调整路由与原库存中心共用 /inventory 前缀。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from .models import StockAdjustment
from .schemas import AdjustmentCreate
from .service import cancel_adjustment, confirm_adjustment, create_adjustment, detail

router = APIRouter(prefix="/inventory/adjustments", tags=["inventory"], dependencies=[Depends(require_current_user)])


@router.get("")
def listing(page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    # 明细随单返回，前端可直接预览冻结的原数量和调整后数量。
    rows = db.scalars(select(StockAdjustment).order_by(StockAdjustment.id.desc()).offset((page - 1) * 20).limit(20))
    return {"items": [detail(db, row) for row in rows], "total": db.scalar(select(func.count()).select_from(StockAdjustment)), "page": page, "page_size": 20}


@router.post("", status_code=201)
def create(payload: AdjustmentCreate, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return create_adjustment(db, payload, actor)


@router.post("/{adjustment_id}/confirm")
def confirm(adjustment_id: int, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return confirm_adjustment(db, adjustment_id, actor)


@router.post("/{adjustment_id}/cancel")
def cancel(adjustment_id: int, db: Session = Depends(get_db)):
    return cancel_adjustment(db, adjustment_id)
