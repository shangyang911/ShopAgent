"""消息 HTTP 入口仅负责参数与鉴权，跨模块查询交给 service。"""
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from .schemas import Category, ReadState, ReadWrite
from .service import list_notifications, mark_read, mark_all_read

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def listing(category: Category = "all", state: ReadState = "all", page: int = Query(1, ge=1),
            page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db),
            actor: UserModel = Depends(require_current_user)):
    return list_notifications(db, actor.id, category, state, page, page_size)


@router.post("/read", status_code=204)
def read(payload: ReadWrite, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    mark_read(db, actor, payload.keys)
    return Response(status_code=204)


@router.post("/read-all", status_code=204)
def read_all(db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    mark_all_read(db, actor)
    return Response(status_code=204)
