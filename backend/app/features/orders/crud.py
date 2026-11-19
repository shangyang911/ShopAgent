# [订单事务] 订单状态、收款事实、预留、库存流水与时间线按业务动作统一提交。
"""Order writes atomically commit status, receipt, reservation, ledger and timeline."""
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.features.customers.models import CustomerAddressModel, CustomerModel
from app.features.orders.models import OrderItemModel, OrderModel, OrderStatus
from app.features.orders.schemas import ORDER_STATUS_TRANSITIONS, OrderCreate
from app.features.products.models import ProductModel
from app.features.products.constants import SELLABLE_STATUSES
from app.features.products.sku_inventory import resolve_sku, sku_label, sku_payload
from app.features.operations.common import actor_fields, add_event, digest
from app.features.operations.inventory import move, opening
from app.features.operations.models import OrderEvent, OrderFacts, Receipt, Reservation, utcnow


class OrderCRUD:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_orders(self, status, page, page_size, sort="created_desc", **filters):
        from app.features.operations.order_queries import order_query, order_sort_columns
        statement = order_query(self.db, status=status, **filters).options(selectinload(OrderModel.items), selectinload(OrderModel.facts))
        total = self.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
        rows = self.db.scalars(statement.order_by(*order_sort_columns(sort)).offset((page - 1) * page_size).limit(page_size)).all()
        return list(rows), total

    def get_order(self, order_id):
        return self.db.scalar(select(OrderModel).options(selectinload(OrderModel.items), selectinload(OrderModel.facts)).where(OrderModel.id == order_id).execution_options(populate_existing=True))

    def create_order(self, payload: OrderCreate, actor=None, idempotency_key=None):
        """校验地址与库存，冻结价格/地址，并在同一事务中写入订单、预留和事件。"""
        payload_hash = digest(sku_payload(payload))
        # 同一幂等键表示同一次提交：重试返回原订单，换内容却复用旧键则拒绝。
        if idempotency_key:
            previous = self.db.scalar(select(OrderFacts).where(OrderFacts.creation_key == idempotency_key))
            if previous:
                if previous.creation_hash != payload_hash:
                    return None, "Idempotency key reused with different order data"
                return self.get_order(previous.order_id), None
        customer = self.db.get(CustomerModel, payload.customer_id)
        address = self.db.get(CustomerAddressModel, payload.address_id)
        if customer is None:
            return None, "Customer not found"
        if address is None or address.customer_id != customer.id:
            return None, "Address does not belong to customer"
        products = {}
        # 多商品统一升序加锁；重复选择同一 SKU 合并成一行，不同 SKU 保持独立。
        for product_id in sorted({item.product_id for item in payload.items}):
            product = self.db.scalar(select(ProductModel).where(ProductModel.id == product_id).with_for_update().execution_options(populate_existing=True))
            if product is None:
                return None, f"Product {product_id} not found"
            if product.status not in SELLABLE_STATUSES:
                return None, f"Product {product_id} is not sellable"
            products[product_id] = product
        quantities, skus = {}, {}
        for item in payload.items:
            sku = resolve_sku(self.db, products[item.product_id], item.sku_id, sellable=True)
            quantities[sku.id] = quantities.get(sku.id, 0) + item.quantity
            skus[sku.id] = sku
        for sid, quantity in quantities.items():
            if skus[sid].stock < quantity:
                if idempotency_key:
                    self.db.rollback()
                    replay = self.db.scalar(select(OrderFacts).where(OrderFacts.creation_key == idempotency_key))
                    if replay and replay.creation_hash == payload_hash:
                        return self.get_order(replay.order_id), None
                return None, f"Insufficient stock for product SKU {sid}"
        subtotal = sum((skus[sid].price * quantity for sid, quantity in quantities.items()), Decimal("0.00"))
        if subtotal + payload.shipping_fee > Decimal('9999999999.99'):
            return None, 'Order total exceeds the supported amount'
        now = utcnow()
        # 收货信息和成交价格随订单冻结；后续修改客户地址、商品价格不会改写历史订单。
        order = OrderModel(order_number=f"ORD-{uuid4().hex[:16].upper()}", customer_id=customer.id, address_id=address.id,
                           status=OrderStatus.PENDING_PAYMENT, subtotal=subtotal, shipping_fee=payload.shipping_fee,
                           total=subtotal + payload.shipping_fee, note=payload.note,
                           recipient_name=address.recipient_name, recipient_phone=address.phone, country=address.country,
                           province=address.province, city=address.city, district=address.district, address_detail=address.detail,
                           postal_code=address.postal_code, longitude=address.longitude, latitude=address.latitude,
                           created_at=now, updated_at=now)
        self.db.add(order)
        for sid, quantity in quantities.items():
            sku = skus[sid]
            product = products[sku.product_id]
            order.items.append(OrderItemModel(product_id=product.id, product_name=product.name, quantity=quantity,
                                              sku_id=sku.id, sku_code=sku.code, sku_label=sku_label(sku),
                                              unit_price=sku.price, line_total=sku.price * quantity))
        self.db.flush()
        self.db.add(OrderFacts(order_id=order.id, created_at=now, creation_key=idempotency_key, creation_hash=payload_hash))
        movement_ids = []
        for item in sorted(order.items, key=lambda i: i.product_id):
            product = products[item.product_id]
            opening(self.db, product, actor)
            self.db.add(Reservation(order_id=order.id, order_item_id=item.id, product_id=product.id,
                                    quantity=item.quantity, status="active", created_at=now))
            movement = move(self.db, product, kind="order_reserve", available=-item.quantity, reserved=item.quantity,
                            reference_type="order", reference_id=order.id, reference_number=order.order_number,
                            key=f"reserve:{item.id}", actor=actor, now=now, sku_id=item.sku_id)
            movement_ids.append(movement.id)
        add_event(self.db, order, "created", "创建订单并预留库存", actor, key=f"created:{order.id}", occurred_at=now, details={"movement_ids": movement_ids})
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            previous = self.db.scalar(select(OrderFacts).where(OrderFacts.creation_key == idempotency_key)) if idempotency_key else None
            if previous and previous.creation_hash == payload_hash:
                return self.get_order(previous.order_id), None
            raise
        return self.get_order(order.id), None

    def update_status(self, order_id, next_status, actor=None, reason=None, idempotency_key=None, shipment_revision=None, *, commit=True):
        """统一处理订单状态及其库存、收款、时间线副作用；配送和客服也复用此方法。"""
        # 锁定后强制刷新 ORM 缓存，确保状态校验针对数据库最新值，而非旧页面快照。
        order = self.db.scalar(select(OrderModel).where(OrderModel.id == order_id).with_for_update().execution_options(populate_existing=True))
        if order is None:
            return None, "Order not found"
        event_key = f"status:{idempotency_key}" if idempotency_key else None
        if event_key:
            event = self.db.scalar(select(OrderEvent).where(OrderEvent.idempotency_key == event_key).with_for_update())
            if event:
                if (event.order_id != order_id or event.to_status != next_status.value or event.reason != reason
                    or (shipment_revision is not None and event.details.get("shipment_revision") != shipment_revision)):
                    return order, "Idempotency key reused with different action"
                return self.get_order(order_id), None
        if next_status == OrderStatus.PAID_PENDING_SHIPMENT and self.db.scalar(select(Receipt.id).where(Receipt.order_id == order_id).with_for_update()):
            return self.get_order(order_id), None
        if next_status not in ORDER_STATUS_TRANSITIONS[order.status]:
            return order, f"Cannot change status from {order.status.value} to {next_status.value}"
        from app.features.shipping.parcels.service import assert_no_label_pending
        from fastapi import HTTPException
        try:
            assert_no_label_pending(self.db, order_id)
        except HTTPException as error:
            return order, error.detail
        shipment = None
        if next_status == OrderStatus.IN_DELIVERY:
            # 未结售后先暂停交接；当前读可看见本请求早先快照之后新建的售后。
            from app.features.aftersales.models import AfterSale
            if self.db.scalar(select(AfterSale.id).where(AfterSale.order_id == order_id, AfterSale.status == "open").with_for_update()):
                return order, "订单有处理中售后，请先完成或撤销售后再交接"
            from app.features.shipping.fulfillment.models import ShipmentModel
            shipment = self.db.scalar(select(ShipmentModel).where(ShipmentModel.order_id == order_id).with_for_update().execution_options(populate_existing=True))
            if shipment is None:
                return order, "请先在发货工作台登记快递公司和运单号，再确认实物交接出库"
            if shipment_revision is not None and shipment.revision != shipment_revision:
                return order, "快递信息已变化，请刷新并重新核对后交接"
        facts = self.db.scalar(select(OrderFacts).where(OrderFacts.order_id == order.id).with_for_update().execution_options(populate_existing=True))
        if facts is None:
            facts = OrderFacts(order_id=order.id)
            self.db.add(facts)
            add_event(self.db, order, "historical_import", "历史数据接入：原业务时间未知", key=f"import:{order.id}", historical=True)
        now = utcnow()
        before = order.status.value
        details = {}
        if next_status == OrderStatus.PAID_PENDING_SHIPMENT:
            receipt = Receipt(order_id=order.id, amount=order.total, occurred_at=now,
                              idempotency_key=f"receipt:{order.id}", **actor_fields(actor))
            self.db.add(receipt)
            self.db.flush()
            facts.paid_at = now
            details = {"receipt_id": receipt.id, "amount": str(receipt.amount)}
        movement_ids = []
        if next_status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.IN_DELIVERY):
            products = {}
            # 先锁商品，再锁预留行，与商品删除采用一致顺序；已结束的预留不会再次释放。
            product_ids = set(self.db.scalars(select(Reservation.product_id).where(Reservation.order_id == order.id)))
            for pid in sorted(product_ids):
                product = self.db.scalar(select(ProductModel).where(ProductModel.id == pid).with_for_update().execution_options(populate_existing=True))
                if product is not None:
                    products[pid] = product
            reservations = list(self.db.scalars(select(Reservation).where(Reservation.order_id == order.id, Reservation.status == "active").order_by(Reservation.product_id, Reservation.id).with_for_update().execution_options(populate_existing=True)))
            for reservation in reservations:
                if reservation.product_id not in products:
                    return order, f"Product {reservation.product_id} not found"
                item = self.db.get(OrderItemModel, reservation.order_item_id)
                dispatch = next_status == OrderStatus.IN_DELIVERY
                product = products[reservation.product_id]
                movement = move(self.db, product, kind="dispatch" if dispatch else "release",
                                available=0 if dispatch else reservation.quantity, reserved=-reservation.quantity,
                                reference_type="order", reference_id=order.id, reference_number=order.order_number,
                                key=f"end_reservation:{reservation.id}", actor=actor, reason=reason, now=now, sku_id=item.sku_id if item else None)
                reservation.status = "dispatched" if dispatch else "released"
                reservation.ended_at = now
                movement_ids.append(movement.id)
        if movement_ids:
            details["movement_ids"] = movement_ids
        if next_status == OrderStatus.IN_DELIVERY:
            facts.dispatch_started_at = now
            shipment.dispatched_at = now
            shipment.dispatched_by = actor.username if actor else "system"
            details.update(carrier_name=shipment.carrier_name, tracking_number=shipment.tracking_number,
                           shipment_revision=shipment.revision)
            from app.features.shipping.parcels.service import parcel_rows
            details["packages"] = [{"package_no": no, "carrier_name": row.carrier_name,
                                    "tracking_number": row.tracking_number}
                                   for no, row in parcel_rows(self.db, order_id)[1]]
        elif next_status == OrderStatus.COMPLETED:
            facts.completed_at = now
        elif next_status == OrderStatus.CANCELLED:
            # 已付款未发货也允许直接取消并释放预留；退款凭据由售后另行登记。
            # 取消履约本身不能生成退款事实，也不能删除原收款记录。
            facts.cancelled_at = now
        order.status = next_status
        order.updated_at = now
        labels = {OrderStatus.PAID_PENDING_SHIPMENT: "手工确认收款（未与银行对账）", OrderStatus.CANCELLED: "取消订单",
                  OrderStatus.IN_DELIVERY: "交接快递并确认出库", OrderStatus.COMPLETED: "人工确认订单完成（非快递签收回执）", OrderStatus.REFUNDED: "订单标记退款；退款金额和时间待核实"}
        add_event(self.db, order, next_status.value, labels[next_status], actor,
                  key=event_key or f"transition:{order.id}:{next_status.value}", before=before, reason=reason, details=details, occurred_at=now)
        # 售后全额退款可把取消履约纳入外层事务，任一步失败均不释放库存。
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return self.get_order(order_id), None
