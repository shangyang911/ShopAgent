r"""Seed 100 labelled orders through business services; repeat runs reuse the batch.

Run from backend: .venv\Scripts\python.exe scripts/seed_core_test_data.py --batch TEST-20261028
Only simulated facts are backdated; their events retain the actual injection time/source.
All service commits are savepoints inside one outer transaction.
"""
import argparse
from collections import Counter
from datetime import timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import engine
from app.features.customers.models import CustomerAddressModel, CustomerModel
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.orders.schemas import OrderCreate
from app.features.products.models import ProductModel
from app.features.shipping.fulfillment.service import save_shipment
from app.features.shipping.fulfillment.schemas import ShipmentWrite
from app.features.operations.common import add_event, wire
from app.features.operations.inventory import evaluate_alert, opening
from app.features.operations.inventory_router import InboundPayload, confirm_inbound, create_inbound, void_inbound
from app.features.operations.models import Inbound, InventoryRule, Movement, OrderEvent, PrintDocument, Receipt, Reservation, RuleEvent, Setting, StockAlert, utcnow
from app.features.operations.orders_router import PrintPayload
from app.features.operations.printing import request_print
from demo_customer_profiles import DEMO_CUSTOMERS, address_values

SEED_ACTOR = SimpleNamespace(id=None, username="测试数据生成器")
STATES = ["pending_payment"] * 4 + ["paid_pending_shipment"] * 5 + ["in_delivery"] * 3 + ["completed"] * 5 + ["cancelled"] * 2 + ["refunded"]
PRODUCT_NAMES = ["无线键盘", "蓝牙鼠标", "桌面音箱", "USB 扩展坞", "充电线", "保温杯", "收纳盒", "护眼台灯", "运动水壶", "帆布包", "便携风扇", "手机支架", "厨房计时器", "玻璃饭盒", "旅行收纳袋", "记事本", "中性笔套装", "桌面置物架", "护腕垫", "无线充电器"]


def make_plan(now):
    plans, timeline = [], []
    for index in range(100):
        state = STATES[index % 20]
        refund_shipped = state == "refunded" and (index // 20) % 2 == 1
        release = state == "cancelled" or (state == "refunded" and not refund_shipped)
        overdue = state == "paid_pending_shipment" and index < 40
        age = timedelta(hours=36 + index % 4) if release else timedelta(hours=30 + index % 3) if overdue else timedelta(hours=1 + index % 16, minutes=index * 7 % 30)
        plan = {"index": index, "state": state, "release": release, "refund_shipped": refund_shipped,
                "created_at": now - age, "items": [((index + j * 7) % 20, 1 + (index + j) % 3) for j in range(1 + index % 3)]}
        plans.append(plan)
        actions = [(0, "create")]
        paid = state not in ("pending_payment", "cancelled") or (state == "cancelled" and index % 2 == 0)
        if paid:
            actions.append((10, "pay"))
        if state in ("paid_pending_shipment", "in_delivery", "completed") and index % 2 == 0:
            actions.append((15, "print"))
            if state == "paid_pending_shipment" and index % 10 == 4:
                actions.append((18, "reprint"))
        if state in ("in_delivery", "completed") or refund_shipped:
            actions.append((20, "dispatch"))
        if state == "completed":
            actions.append((35, "complete"))
        if state in ("cancelled", "refunded"):
            actions.append((35, "cancel" if state == "cancelled" else "refund"))
        if index % 5 == 0:
            actions.append((40, "note"))
        timeline.extend((plan["created_at"] + timedelta(minutes=minutes), index, kind) for minutes, kind in actions)
    return plans, sorted(timeline)


def seed(bind, batch, dry_run=False):
    now = utcnow()
    key = f"test_seed:{batch}"
    with Session(bind) as db:
        existing = db.get(Setting, key)
        if existing:
            return {**json.loads(existing.value), "already_exists": True}
    plans, timeline = make_plan(now)
    target = [0 if index in (0, 4, 9, 12) else 3 if index in (1, 5, 10, 13, 18) else 20 for index in range(20)]
    inbound_products = [2, 3, 6, 8]
    incoming = {index: 5 for index in inbound_products}
    consumed = Counter()
    for plan in plans:
        if not plan["release"]:
            consumed.update(dict(plan["items"]))
    initial = [consumed[i] + target[i] - incoming.get(i, 0) for i in range(20)]
    balance = initial.copy()
    for _, index, kind in timeline:
        if kind == "create" or kind in ("cancel", "refund") and plans[index]["release"]:
            for pid, quantity in plans[index]["items"]:
                balance[pid] += -quantity if kind == "create" else quantity
                if balance[pid] < 0:
                    raise RuntimeError("Fixture plan would overdraw stock")
    if dry_run:
        return {"batch": batch, "orders": 100, "products": 20, "customers": 10, "inbounds": 10,
                "statuses": dict(Counter(p["state"] for p in plans)), "dry_run": True}

    # Business service commits release SAVEPOINTs; the outer transaction commits only after verification.
    with bind.connect() as connection, connection.begin():
        with Session(connection, join_transaction_mode="create_savepoint", expire_on_commit=False, autoflush=False) as db:
            original_stocks = dict(db.execute(select(ProductModel.id, ProductModel.stock)).all())
            original_order_ids = set(db.scalars(select(OrderModel.id)))
            coverage = db.get(Setting, "data_coverage_start")
            original_coverage = coverage.value if coverage else None
            marker = Setting(key=key, value='{"state":"preparing"}')
            db.add(marker)
            db.flush()
            image = db.scalar(select(ProductModel.image_url).where(ProductModel.image_url.like('/uploads/%')).limit(1)) or "/favicon.svg"
            products, customers, addresses = [], [], []
            opening_at = min(p["created_at"] for p in plans) - timedelta(hours=1)
            for index, name in enumerate(PRODUCT_NAMES):
                product = ProductModel(name=f"【测试】{batch} {name}", category=["测试·数码", "测试·家居", "测试·文具", "测试·户外"][index % 4],
                    price=Decimal(19 + index * 11) + Decimal("0.90"), stock=initial[index], status="Active", image_url=image,
                    color=["Black", "White", "Blue", "Red"][index % 4], rating=Decimal("4.5"), reviews=0, shipping="Regular")
                db.add(product); db.flush(); products.append(product)
                rule = {"monitor_enabled": index != 18, "threshold_override": 0 if index == 3 else 5}
                db.add(InventoryRule(product_id=product.id, **rule))
                db.add(RuleEvent(product_id=product.id, before={}, after={**rule, "source": "test_seed", "batch": batch}, actor_id=None,
                    actor_name_snapshot=SEED_ACTOR.username, occurred_at=opening_at))
                opening(db, product, SEED_ACTOR)
                movement = db.scalar(select(Movement).where(Movement.idempotency_key == f"opening:product:{product.id}"))
                movement.occurred_at, movement.reason = opening_at, f"【模拟期初】{batch}，仅供测试"
                evaluate_alert(db, product, opening_at)
            for index in range(10):
                profile = DEMO_CUSTOMERS[index]
                customer = CustomerModel(**{field: profile[field] for field in ("name", "phone", "email")})
                db.add(customer); db.flush(); customers.append(customer)
                address = CustomerAddressModel(customer_id=customer.id, **address_values(profile), is_default=True)
                db.add(address); db.flush(); addresses.append(address)
            db.commit()
            order_ids, document_ids, jobs = {}, {}, []
            crud = OrderCRUD(db)
            state_actions = {"pay": OrderStatus.PAID_PENDING_SHIPMENT, "dispatch": OrderStatus.IN_DELIVERY,
                "complete": OrderStatus.COMPLETED, "cancel": OrderStatus.CANCELLED, "refund": OrderStatus.REFUNDED}
            for at, index, kind in timeline:
                plan = plans[index]
                with patch("app.features.orders.crud.utcnow", return_value=at):
                    if kind == "create":
                        payload = OrderCreate(customer_id=customers[index % 10].id, address_id=addresses[index % 10].id,
                            items=[{"product_id": products[p].id, "quantity": q} for p, q in plan["items"]],
                            shipping_fee=Decimal("0.00") if index % 3 == 0 else Decimal("6.00"),
                            note=f"【模拟测试数据】{batch} 第{index + 1:03d}单；不代表真实交易，不执行实际配送。")
                        order, error = crud.create_order(payload, SEED_ACTOR, f"{batch}:order:{index}")
                        if error:
                            raise RuntimeError(error)
                        order_ids[index] = order.id
                        order.order_number = f"{batch}-{index + 1:04d}"
                        for movement in db.scalars(select(Movement).where(Movement.reference_type == "order", Movement.reference_id == order.id)):
                            movement.reference_number = order.order_number
                        db.commit()
                    elif kind in state_actions:
                        if kind == "dispatch":
                            save_shipment(db, order_ids[index], ShipmentWrite(carrier_name="模拟快递（测试）",
                                tracking_number=f"TEST-{batch}-{index:04d}", remark="模拟运单，请勿真实配送", expected_revision=0), SEED_ACTOR)
                        _, error = crud.update_status(order_ids[index], state_actions[kind], SEED_ACTOR,
                            f"【测试】{batch} 模拟{kind}" if kind in ("cancel", "refund") else None, f"{batch}:{index}:{kind}")
                        if error:
                            raise RuntimeError(error)
                    elif kind in ("print", "reprint"):
                        payload = PrintPayload(order_ids=[order_ids[index]], idempotency_key=f"{batch}:{index}:{kind}",
                            source_documents={order_ids[index]: document_ids[index]} if kind == "reprint" else {},
                            picking_note=f"【测试配货】{batch}，请勿真实配送", reprint_reason="模拟缺纸后补打" if kind == "reprint" else "")
                        with patch("app.features.operations.printing.utcnow", return_value=at):
                            job = request_print(db, payload, SEED_ACTOR)
                        jobs.append(job.id)
                        document_ids[index] = db.scalar(select(PrintDocument.id).where(PrintDocument.job_id == job.id))
                    else:
                        order = crud.get_order(order_ids[index])
                        add_event(db, order, "note_added", f"【测试备注】{batch} 已模拟核对商品与地址", SEED_ACTOR,
                            key=f"{batch}:note:{index}", occurred_at=at)
                        db.commit()
            inbound_ids = []
            for index in range(10):
                pid = inbound_products[index] if index < 4 else (index * 2) % 20
                payload = InboundPayload(items=[{"product_id": products[pid].id, "quantity": 5}],
                    remark=f"【测试补货】{batch} 第{index + 1}单", idempotency_key=f"{batch}:inbound:{index}")
                row = create_inbound(payload, db, SEED_ACTOR)
                inbound_ids.append(row["id"])
                db.get(Inbound, row["id"]).created_at = now - timedelta(minutes=20)
                db.commit()
                if index < 4:
                    with patch("app.features.operations.inventory_router.utcnow", return_value=now - timedelta(minutes=10)):
                        confirm_inbound(row["id"], db, SEED_ACTOR)
                elif index >= 8:
                    void_inbound(row["id"], db)

            ids = list(order_ids.values())
            for event in db.scalars(select(OrderEvent).where(OrderEvent.order_id.in_(ids))):
                event.actor_type = "system"
                event.details = {**event.details, "source": "test_seed", "batch": batch, "simulated": True, "injected_at": wire(now)}
            for receipt in db.scalars(select(Receipt).where(Receipt.order_id.in_(ids))):
                receipt.source = "test_seed"
            db.flush()
            # Verify both ledger dimensions and preservation of existing business data before committing.
            for index, product in enumerate(products):
                db.refresh(product)
                available, reserved_delta = db.execute(select(func.sum(Movement.available_delta), func.sum(Movement.reserved_delta)).where(Movement.product_id == product.id)).one()
                reserved = db.scalar(select(func.coalesce(func.sum(Reservation.quantity), 0)).where(Reservation.product_id == product.id, Reservation.status == 'active'))
                if product.stock != target[index] or available != product.stock or reserved_delta != reserved:
                    raise RuntimeError(f"Stock ledger mismatch: {product.id}")
            if dict(db.execute(select(ProductModel.id, ProductModel.stock).where(ProductModel.id.in_(original_stocks))).all()) != original_stocks:
                raise RuntimeError("Original product inventory changed")
            if set(db.scalars(select(OrderModel.id).where(OrderModel.id.not_in(ids)))) != original_order_ids:
                raise RuntimeError("Original order membership changed")
            if (db.get(Setting, 'data_coverage_start').value if original_coverage else None) != original_coverage:
                raise RuntimeError('Data coverage marker changed')
            states = dict(db.execute(select(OrderModel.status, func.count()).where(OrderModel.id.in_(ids)).group_by(OrderModel.status)).all())
            if sum(states.values()) != 100:
                raise RuntimeError('Expected exactly 100 orders')
            active_alerts = list(db.scalars(select(StockAlert).where(StockAlert.active_product_id.in_([p.id for p in products]))))
            report = {"batch": batch, "injected_at": wire(now), "orders": 100, "products": 20, "customers": 10, "addresses": 10,
                "statuses": {state.value: count for state, count in states.items()}, "receipts": db.scalar(select(func.count()).select_from(Receipt).where(Receipt.order_id.in_(ids))),
                "inbounds": {"confirmed": 4, "draft": 4, "void": 2}, "print_requests": len(jobs),
                "alerts": dict(Counter(alert.severity for alert in active_alerts)), "order_ids": [order_ids[i] for i in range(100)],
                "product_ids": [p.id for p in products], "customer_ids": [c.id for c in customers], "address_ids": [a.id for a in addresses],
                "inbound_ids": inbound_ids, "print_job_ids": jobs, "original_products_unchanged": True, "already_exists": False}
            marker.value = json.dumps(report, ensure_ascii=False)
            db.commit()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="TEST-" + utcnow().replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y%m%d"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"TEST-[A-Z0-9-]{1,32}", args.batch):
        parser.error("batch must begin TEST- and contain only uppercase ASCII letters, numbers and hyphens")
    result = seed(engine, args.batch, args.dry_run)
    if not args.dry_run:
        output = Path(__file__).resolve().parents[2] / ".local-test" / f"seed-{args.batch}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if not key.endswith('_ids')}, ensure_ascii=False))
