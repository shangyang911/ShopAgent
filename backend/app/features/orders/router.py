"""订单与订单商品 HTTP 接口。"""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from app.features.auth.models import UserModel
from app.features.operations.common import utc_input
from app.features.operations.exports import csv_response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.orders.schemas import Order, OrderCreate, OrderListResponse, OrderStatusUpdate
from .filtering import OrderFilterStatus, status_counts

router = APIRouter(prefix="/orders", tags=["orders"], dependencies=[Depends(require_current_user)])


def search_filters(
    order_status: OrderFilterStatus | None = Query(default=None, alias="status"),
    statuses: list[OrderFilterStatus] | None = Query(default=None),
    keyword: str = Query(default="", max_length=180),
    time_field: Literal['created_at', 'paid_at', 'completed_at'] = 'created_at',
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    as_of: datetime | None = None,
    min_total: Decimal | None = Query(default=None, ge=0, le=Decimal('9999999999.99'), max_digits=12, decimal_places=2),
    max_total: Decimal | None = Query(default=None, ge=0, le=Decimal('9999999999.99'), max_digits=12, decimal_places=2),
    print_request_status: Literal['never', 'requested', 'multiple'] | None = None,
    overdue: bool = False,
    unknown_time: bool = False,
):
    return dict(status=order_status, statuses=statuses, keyword=keyword,
        time_field=time_field, start_at=utc_input(start_at), end_at=utc_input(end_at), as_of=utc_input(as_of),
        min_total=min_total, max_total=max_total, print_request_status=print_request_status, overdue=overdue, unknown_time=unknown_time)


@router.get("", response_model=OrderListResponse)
def list_orders(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                sort: Literal['created_desc', 'created_asc'] = 'created_desc',
                filters: dict = Depends(search_filters), db: Session = Depends(get_db)):
    rows, total = OrderCRUD(db).list_orders(page=page, page_size=page_size, sort=sort, **filters)
    from app.features.operations.order_queries import order_query
    from app.features.orders.models import OrderModel
    ids = order_query(db, **{key: value for key, value in filters.items() if key not in ('status', 'statuses')}).with_only_columns(OrderModel.id)
    return OrderListResponse(items=rows, total=total, page=page, page_size=page_size, status_counts=status_counts(db, ids))


@router.get("/export")
def export_orders(filters: dict = Depends(search_filters), sort: Literal['created_desc', 'created_asc'] = 'created_desc',
                  db: Session = Depends(get_db)):
    from app.features.operations.order_queries import order_query, order_sort_columns
    from app.features.operations.models import OrderFacts
    from app.features.orders.models import OrderModel, OrderItemModel
    statement = order_query(db, **filters).add_columns(OrderItemModel, OrderFacts).join(OrderItemModel, OrderItemModel.order_id == OrderModel.id)
    statement = statement.order_by(*order_sort_columns(sort), OrderItemModel.id)
    columns = ('order_id', 'order_number', 'status', 'customer_id', 'recipient_name', 'recipient_phone', 'address',
               'created_at', 'paid_at', 'completed_at', 'subtotal', 'shipping_fee', 'total', 'print_request_count',
               'order_item_id', 'product_id', 'product_name_snapshot', 'quantity', 'unit_price', 'line_total',
               'refunded_amount', 'aftersale_pending', 'sku_id', 'sku_code', 'sku_label')
    def rows():
        for order, item, facts in db.execute(statement.execution_options(yield_per=500)):
            yield [order.id, order.order_number, order.status, order.customer_id, order.recipient_name, order.recipient_phone,
                   f'{order.country} {order.province}{order.city}{order.district}{order.address_detail}',
                   facts.created_at if facts else None, facts.paid_at if facts else None, facts.completed_at if facts else None,
                   order.subtotal, order.shipping_fee, order.total, order.print_request_count,
                   item.id, item.product_id, item.product_name, item.quantity, item.unit_price, item.line_total,
                   order.refunded_amount, order.aftersale_pending, item.sku_id, item.sku_code, item.sku_label]
    return csv_response('orders.csv', columns, rows())


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: int, db: Session = Depends(get_db)):
    row = OrderCRUD(db).get_order(order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


@router.post("", response_model=Order, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user),
                 idempotency_key: str | None = Header(default=None, min_length=8, max_length=100)):
    row, error = OrderCRUD(db).create_order(payload, actor, idempotency_key)
    if error:
        code = status.HTTP_409_CONFLICT if error.startswith(("Insufficient stock", "Idempotency")) else status.HTTP_404_NOT_FOUND if error.endswith("not found") else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(status_code=code, detail=error)
    return row


@router.patch("/{order_id}/status", response_model=Order)
def update_order_status(order_id: int, payload: OrderStatusUpdate, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    # 历史 refunded 状态仍可读取；新退款必须有金额、发生时间和凭据。
    if payload.status == OrderStatus.REFUNDED:
        raise HTTPException(409, "请使用订单售后登记实际退款，不能仅修改退款状态")
    if payload.status == OrderStatus.IN_DELIVERY:
        raise HTTPException(409, "请前往发货工作台登记运单，并通过交接接口确认实物出库")
    if payload.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED) and not (payload.reason or '').strip():
        raise HTTPException(422, '取消或退款必须填写原因')
    row, error = OrderCRUD(db).update_status(order_id, payload.status, actor, payload.reason, payload.idempotency_key)
    if error:
        code = status.HTTP_404_NOT_FOUND if row is None else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=error)
    return row
