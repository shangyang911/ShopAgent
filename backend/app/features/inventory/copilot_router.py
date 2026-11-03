# [运营建议接口] 分析只读；用户核对后，复算建议并复用现有入库草稿流程。
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.auth.models import UserModel
from app.features.products.models import ProductModel
from app.features.operations.inventory_router import InboundPayload, create_inbound
from app.features.operations.models import Inbound
from app.features.operations.common import digest
from app.features.products.sku_inventory import sku_payload
from .copilot import replenishment, shipping_analysis

router = APIRouter(prefix='/copilot', tags=['copilot'], dependencies=[Depends(require_current_user)])


class PlanParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    keyword: str = Field(default='', max_length=180)
    coverage_days: int = Field(default=7, ge=1, le=90)
    safety_percent: int = Field(default=15, ge=0, le=100)
    minimum_stock: int = Field(default=5, ge=0, le=10000)


class PlanLine(BaseModel):
    # 客户端同时提交建议来源摘要，服务端不能只信任页面传来的数量。
    model_config = ConfigDict(extra='forbid')
    product_id: int = Field(gt=0)
    sku_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1000000)
    basis: str = Field(min_length=64, max_length=64)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parameters: PlanParameters
    items: list[PlanLine] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)


@router.get('/replenishment')
def analyze(keyword: str = '', coverage_days: int = Query(7, ge=1, le=90), safety_percent: int = Query(15, ge=0, le=100),
            minimum_stock: int = Query(5, ge=0, le=10000), db: Session = Depends(get_db)):
    return replenishment(db, keyword, coverage_days, safety_percent, minimum_stock)


@router.get('/shipping-analysis')
def analyze_shipping(db: Session = Depends(get_db)):
    return shipping_analysis(db)


@router.post('/replenishment/draft')
def draft(payload: DraftRequest, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    if len({i.sku_id for i in payload.items}) != len(payload.items):
        raise HTTPException(422, '同一SKU不能重复选择')
    request_hash = digest(payload.model_dump(exclude={'idempotency_key'}))
    inbound = InboundPayload(idempotency_key=payload.idempotency_key,
        remark=f'运营Copilot补货建议；仅草稿，不代表采购在途。核对来源：{request_hash}',
        items=[{'product_id': i.product_id, 'sku_id': i.sku_id, 'quantity': i.quantity} for i in payload.items])
    # 与现有库存操作保持相同商品加锁顺序，降低并发提交产生死锁的风险。
    list(db.scalars(select(ProductModel).where(ProductModel.id.in_(sorted({i.product_id for i in payload.items}))).order_by(ProductModel.id).with_for_update().execution_options(populate_existing=True)))
    previous = db.scalar(select(Inbound).where(Inbound.creation_key == payload.idempotency_key).with_for_update())
    if previous:
        # 网络响应丢失后的原内容重试返回既有草稿，同键换内容则拒绝。
        expected = digest({k: v for k, v in sku_payload(inbound).items() if k != 'idempotency_key'})
        if previous.creation_hash != expected:
            raise HTTPException(409, '幂等键已用于其他建议，请重新分析')
        from app.features.operations.inventory_router import inbound_detail
        return inbound_detail(db, previous)
    current = {row['sku_id']: row for row in replenishment(db, **payload.parameters.model_dump())['items']}
    # 复算后核对商品归属、来源摘要和建议数量，防止过期页面或篡改数量直接写入。
    for item in payload.items:
        row = current.get(item.sku_id)
        if not row or row['product_id'] != item.product_id or row['basis'] != item.basis or row['suggested_quantity'] != item.quantity:
            raise HTTPException(409, '库存、需求或草稿计划已变化，请重新分析核对后创建')
    return create_inbound(inbound, db, actor)
