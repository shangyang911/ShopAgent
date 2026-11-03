from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.products.models import ProductModel
from app.features.products.sku_inventory import resolve_sku, sku_payload
from .common import actor_fields, digest, wire
from .exports import csv_response
from .inventory import default_threshold, evaluate_alert, inventory_query, inventory_rows, move, movement_query, opening, paged, reconcile_alerts, rule_values
from .models import Inbound, InboundLine, InventoryRule, RuleEvent, Setting, StockAlert, utcnow

router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(require_current_user)])


class RuleUpdate(BaseModel):
    monitor_enabled: bool = True
    threshold_override: int | None = Field(default=None, ge=0, le=1000000)


class DefaultRuleUpdate(BaseModel):
    threshold: int = Field(ge=0, le=1000000)


class InboundItem(BaseModel):
    product_id: int = Field(gt=0)
    sku_id: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0, le=1000000, strict=True)


class InboundPayload(BaseModel):
    remark: str = Field(default="", max_length=2000)
    items: list[InboundItem] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=100)


def inbound_detail(db, row):
    return wire({**wire(row), "items": list(db.scalars(select(InboundLine).where(InboundLine.receipt_id == row.id).order_by(InboundLine.id)))})


def locked_inbound(db, receipt_id):
    row = db.scalar(select(Inbound).where(Inbound.id == receipt_id).with_for_update().execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404, "入库单不存在")
    return row


def set_lines(db, row, payload):
    """草稿可重写明细；先合并同商品数量并完整校验，任一商品无效则整单不保存。"""
    raw_quantities = {}
    for item in payload.items:
        key = (item.product_id, item.sku_id)
        raw_quantities[key] = raw_quantities.get(key, 0) + item.quantity
    if len(raw_quantities) > 100 or any(value > 1000000 for value in raw_quantities.values()):
        raise HTTPException(422, '每单最多 100 个 SKU，合并后每个 SKU 最多 1000000 件')
    products = {}
    for pid in sorted({item.product_id for item in payload.items}):
        product = db.scalar(select(ProductModel).where(ProductModel.id == pid).with_for_update().execution_options(populate_existing=True))
        if not product:
            raise HTTPException(409, f"Product {pid} no longer exists")
        products[pid] = product
    quantities = {}
    for item in payload.items:
        sku = resolve_sku(db, products[item.product_id], item.sku_id, sellable=True)
        key = (item.product_id, sku.id)
        quantities[key] = quantities.get(key, 0) + item.quantity
    if len(quantities) > 100 or any(quantity > 1000000 for quantity in quantities.values()):
        raise HTTPException(422, "At most 100 SKU lines, each at most 1000000 units")
    db.execute(delete(InboundLine).where(InboundLine.receipt_id == row.id))
    for (pid, sid), quantity in quantities.items():
        db.add(InboundLine(receipt_id=row.id, product_id=pid, sku_id=sid, product_name_snapshot=products[pid].name, quantity=quantity))
    row.remark = payload.remark.strip()
    db.flush()


@router.get("")
def list_inventory(q: str = "", category: str | None = None,
                   severity: Literal['risk', 'low_stock', 'out_of_stock', 'normal'] | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return inventory_rows(db, inventory_query(db, q, category, severity), page, page_size)


@router.get("/settings")
def settings(db: Session = Depends(get_db)):
    return {"default_threshold": default_threshold(db)}


@router.put("/settings")
def update_settings(payload: DefaultRuleUpdate, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    # All mutations lock products before rules/settings. Keep the global edit in that order too.
    list(db.scalars(select(ProductModel).order_by(ProductModel.id).with_for_update()))
    row = db.scalar(select(Setting).where(Setting.key == "default_stock_threshold").with_for_update())
    before = default_threshold(db)
    if row is None:
        row = Setting(key="default_stock_threshold", value=str(payload.threshold))
        db.add(row)
    else:
        row.value = str(payload.threshold)
    db.add(RuleEvent(product_id=None, before={"default_threshold": before}, after={"default_threshold": payload.threshold}, **actor_fields(actor)))
    db.flush()
    reconcile_alerts(db)
    db.commit()
    return {"default_threshold": payload.threshold}


@router.put("/products/{product_id}/rule")
def update_rule(product_id: int, payload: RuleUpdate, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    product = db.scalar(select(ProductModel).where(ProductModel.id == product_id).with_for_update().execution_options(populate_existing=True))
    if product is None:
        raise HTTPException(404, "商品不存在")
    before = rule_values(db, product_id)
    rule = db.get(InventoryRule, product_id)
    if rule is None:
        rule = InventoryRule(product_id=product_id)
        db.add(rule)
    rule.monitor_enabled = payload.monitor_enabled
    rule.threshold_override = payload.threshold_override
    db.add(RuleEvent(product_id=product_id, before=before, after=payload.model_dump(), **actor_fields(actor)))
    db.flush()
    evaluate_alert(db, product)
    db.commit()
    return rule_values(db, product_id)


@router.get("/rule-events")
def rule_events(product_id: int | None = None, page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    statement = select(RuleEvent).order_by(RuleEvent.id.desc())
    if product_id:
        statement = statement.where(RuleEvent.product_id == product_id)
    return paged(db, statement, page, 20)


@router.get("/movements")
def movements(product_id: int | None = None, movement_type: str | None = None,
              reference_number: str = "", actor: str = "", start_at: datetime | None = None,
              end_at: datetime | None = None, order_id: int | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    statement = movement_query(product_id, movement_type, reference_number, actor, start_at, end_at, order_id)
    return paged(db, statement, page, page_size)


@router.get("/movements/export")
def export_movements(product_id: int | None = None, movement_type: str | None = None,
                     reference_number: str = "", actor: str = "", start_at: datetime | None = None,
                     end_at: datetime | None = None, order_id: int | None = None, db: Session = Depends(get_db)):
    statement = movement_query(product_id, movement_type, reference_number, actor, start_at, end_at, order_id)
    columns = ("id", "product_id", "product_name_snapshot", "movement_type", "available_before",
               "available_delta", "available_after", "reserved_delta", "on_hand_delta", "reference_type",
               "reference_id", "reference_number", "reason", "actor_id", "actor_name_snapshot", "occurred_at")
    rows = ([getattr(row, column) for column in columns] for row in db.scalars(statement.execution_options(yield_per=500)))
    return csv_response("inventory-movements.csv", columns, rows)


@router.get("/alerts")
def alerts(alert_status: Literal['active', 'resolved', 'closed'] = "active", severity: Literal['low_stock', 'out_of_stock'] | None = None,
           page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    statement = select(StockAlert).where(StockAlert.alert_status == alert_status).order_by(StockAlert.id.desc())
    if severity:
        statement = statement.where(StockAlert.severity == severity)
    result = paged(db, statement, page, 20)
    ids = [item["product_id"] for item in result["items"]]
    products = {p.id: p for p in db.scalars(select(ProductModel).where(ProductModel.id.in_(ids)))} if ids else {}
    for item in result["items"]:
        product = products.get(item["product_id"])
        item["current_available"] = product.stock if product else None
    return result


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: int, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    row = db.scalar(select(StockAlert).where(StockAlert.id == alert_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "告警不存在")
    if row.acknowledged_at is None:
        row.acknowledged_at = utcnow()
        row.acknowledged_by = actor.username
    db.commit()
    return wire(row)


@router.get("/inbounds")
def inbounds(page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    return paged(db, select(Inbound).order_by(Inbound.id.desc()), page, 20)


@router.get("/inbounds/export")
def export_inbounds(db: Session = Depends(get_db)):
    statement = select(Inbound, InboundLine).join(InboundLine, InboundLine.receipt_id == Inbound.id).order_by(Inbound.id.desc(), InboundLine.id)
    columns = ("receipt_id", "receipt_number", "status", "remark", "created_by", "created_at", "confirmed_by",
               "confirmed_at", "line_id", "product_id", "product_name_snapshot", "quantity")
    rows = ([row.id, row.receipt_number, row.status, row.remark, row.created_by, row.created_at,
             row.confirmed_by, row.confirmed_at, line.id, line.product_id, line.product_name_snapshot, line.quantity]
            for row, line in db.execute(statement.execution_options(yield_per=500)))
    return csv_response("inventory-inbounds.csv", columns, rows)


@router.get("/inbounds/{receipt_id}")
def get_inbound(receipt_id: int, db: Session = Depends(get_db)):
    row = db.get(Inbound, receipt_id)
    if row is None:
        raise HTTPException(404, "入库单不存在")
    return inbound_detail(db, row)


@router.post("/inbounds", status_code=201)
def create_inbound(payload: InboundPayload, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    payload_hash = digest({key: value for key, value in sku_payload(payload).items() if key != "idempotency_key"})
    previous = db.scalar(select(Inbound).where(Inbound.creation_key == payload.idempotency_key))
    if previous:
        if previous.creation_hash != payload_hash:
            raise HTTPException(409, "幂等键已用于其他入库内容")
        return inbound_detail(db, previous)
    row = Inbound(receipt_number=f"IN-{uuid4().hex[:16].upper()}", created_by=actor.username,
                  creation_key=payload.idempotency_key, creation_hash=payload_hash, status="draft")
    db.add(row)
    try:
        db.flush()
        set_lines(db, row, payload)
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = db.scalar(select(Inbound).where(Inbound.creation_key == payload.idempotency_key))
        if previous and previous.creation_hash == payload_hash:
            return inbound_detail(db, previous)
        raise
    return inbound_detail(db, row)


@router.put("/inbounds/{receipt_id}")
def edit_inbound(receipt_id: int, payload: InboundPayload, db: Session = Depends(get_db)):
    row = locked_inbound(db, receipt_id)
    if row.status != "draft":
        raise HTTPException(409, "只有草稿可以修改")
    set_lines(db, row, payload)
    db.commit()
    return inbound_detail(db, row)


@router.delete("/inbounds/{receipt_id}")
def void_inbound(receipt_id: int, db: Session = Depends(get_db)):
    row = locked_inbound(db, receipt_id)
    if row.status != "draft":
        raise HTTPException(409, "只有草稿可以作废")
    row.status = "void"
    db.commit()
    return wire(row)


@router.post("/inbounds/{receipt_id}/confirm")
def confirm_inbound(receipt_id: int, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    row = locked_inbound(db, receipt_id)
    if row.status == "confirmed":
        return inbound_detail(db, row)
    if row.status != "draft":
        raise HTTPException(409, "已作废入库单不能确认")
    lines = list(db.scalars(select(InboundLine).where(InboundLine.receipt_id == row.id).order_by(InboundLine.product_id).with_for_update()))
    if not lines:
        raise HTTPException(409, "空入库单不能确认")
    products = {}
    for line in lines:
        product = db.scalar(select(ProductModel).where(ProductModel.id == line.product_id).with_for_update().execution_options(populate_existing=True))
        if product is None:
            raise HTTPException(409, f"商品 {line.product_id} 已不存在，整单未入库")
        products[line.product_id] = product
        resolve_sku(db, product, line.sku_id, sellable=True)
    now = utcnow()
    for line in lines:
        product = products[line.product_id]
        opening(db, product)
        move(db, product, kind="inbound", available=line.quantity, reserved=0, reference_type="inbound", reference_id=row.id,
             reference_number=row.receipt_number, key=f"inbound:{row.id}:{product.id}:{line.id}", actor=actor, reason=row.remark, now=now, sku_id=line.sku_id)
    row.status, row.confirmed_at, row.confirmed_by = "confirmed", now, actor.username
    db.commit()
    return inbound_detail(db, row)
