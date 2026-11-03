"""包裹清单与附加包裹维护接口。"""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.shipping.parcels.schemas import ParcelWrite
from app.features.shipping.parcels.service import delete_extra, package_snapshot, write_extra


router = APIRouter(prefix="/shipping", tags=["shipping"], dependencies=[Depends(require_current_user)])


@router.get("/orders/{order_id}/packages")
def packages(order_id: int, db: Session = Depends(get_db)):
    return package_snapshot(db, order_id)


@router.put("/orders/{order_id}/packages/{package_no}")
def save_package(order_id: int, payload: ParcelWrite, package_no: int = Path(ge=2, le=100),
                 db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return write_extra(db, order_id, package_no, payload, actor)


@router.delete("/orders/{order_id}/packages/{package_no}")
def remove_package(order_id: int, package_no: int = Path(ge=2, le=100), expected_revision: int = Query(ge=1),
                   db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return delete_extra(db, order_id, package_no, expected_revision, actor)
