"""实际运费登记、汇总与导出接口。"""
import csv
from datetime import datetime
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.operations.common import utc_input
from app.features.shipping.parcels.freight import freight_report
from app.features.shipping.parcels.schemas import FreightWrite
from app.features.shipping.parcels.service import write_freight


router = APIRouter(prefix="/shipping", tags=["shipping"], dependencies=[Depends(require_current_user)])


@router.put("/orders/{order_id}/packages/{package_no}/freight")
def save_freight(order_id: int, payload: FreightWrite, package_no: int = Path(ge=1, le=100),
                 db: Session = Depends(get_db), actor=Depends(require_current_user)):
    return write_freight(db, order_id, package_no, payload, actor)


def report_params(start_at, end_at):
    start_at, end_at = utc_input(start_at), utc_input(end_at)
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(422, "开始时间必须早于结束时间")
    return start_at, end_at


@router.get("/freight-report")
def report(start_at: datetime | None = None, end_at: datetime | None = None,
           carrier: str | None = Query(default=None, max_length=80), db: Session = Depends(get_db)):
    return freight_report(db, *report_params(start_at, end_at), carrier)


@router.get("/freight-report/export")
def export_report(start_at: datetime | None = None, end_at: datetime | None = None,
                  carrier: str | None = Query(default=None, max_length=80), db: Session = Depends(get_db)):
    data = freight_report(db, *report_params(start_at, end_at), carrier)
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(["订单号", "包裹序号", "快递公司", "运单号", "交接时间UTC", "实际运费", "结算状态"])
    def cell(value):
        text = str(value or "")
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text
    for row in data["items"]:
        writer.writerow([cell(row["order_number"]), row["package_no"], cell(row["carrier_name"]), cell(row["tracking_number"]),
                         row["dispatched_at"] or "未知", row["actual_cost"] if row["actual_cost"] is not None else "", "已结算" if row["actual_cost"] is not None else "待结算"])
    return Response(content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="shipping-freight.csv"'})
