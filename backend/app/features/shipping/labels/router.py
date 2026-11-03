"""电子面单配置、取号、核实与打印接口。"""
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.shipping.fulfillment.schemas import BatchDispatch
from app.features.shipping.labels.printing import print_labels
from app.features.shipping.labels.provider import LabelSettings
from app.features.shipping.labels.schemas import LabelCreate, LabelResolve
from app.features.shipping.labels.service import create_label, resolve_label


router = APIRouter(prefix="/shipping", tags=["shipping"], dependencies=[Depends(require_current_user)])


@router.get("/label-settings")
def label_settings():
    settings = LabelSettings.from_env()
    return {"provider": "kuaidi100", "configured": settings.configured,
            "carriers": [code for code, account in settings.accounts.items() if isinstance(account, dict) and account.get("partnerId") and account.get("tempId")]}


@router.post("/orders/{order_id}/packages/{package_no}/label")
async def request_label(order_id: int, payload: LabelCreate, package_no: int = Path(ge=1, le=100),
                        db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return await create_label(db, order_id, package_no, payload, actor)


@router.post("/orders/{order_id}/label-tasks/{task_id}/resolve")
def reconcile_label(order_id: int, task_id: int, payload: LabelResolve,
                    db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return resolve_label(db, order_id, task_id, payload, actor)


@router.post("/labels/print-batch")
def batch_print_labels(payload: BatchDispatch, db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return print_labels(db, payload, actor)
