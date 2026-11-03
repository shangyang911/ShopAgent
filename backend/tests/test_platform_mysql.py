"""真实 MySQL 行锁验收；只创建随机隔离库，不使用业务表。"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import test_order_status as fixtures
from app.features.aftersales.models import RefundRecord
from app.features.aftersales.schemas import CaseCreate, RefundWrite
from app.features.aftersales.service import create_case, record_refund
from app.features.auth.models import UserModel
from app.features.inventory.schemas import AdjustmentCreate
from app.features.inventory.service import confirm_adjustment, create_adjustment
from app.features.operations.models import Movement, utcnow
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.products.models import ProductModel


@unittest.skipUnless(os.getenv("RUN_MYSQL_TESTS") == "1", "Set RUN_MYSQL_TESTS=1 for isolated MySQL tests")
class PlatformMySQLTests(fixtures.OrderFixtures, unittest.TestCase):
    setUpClass = classmethod(fixtures.MySQLConcurrencyTests.setUpClass.__func__)
    drop_test_database = classmethod(fixtures.MySQLConcurrencyTests.drop_test_database.__func__)

    def actor(self):
        with Session(self.engine, expire_on_commit=False) as db:
            actor = UserModel(username="ops-" + uuid4().hex[:20], password_hash="unused")
            db.add(actor)
            db.commit()
            return actor

    def test_two_refund_requests_cannot_spend_the_same_balance(self):
        oid, pid, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            case = create_case(db, oid, CaseCreate(kind="refund", requested_amount="20.00", reason="并发核对", idempotency_key=str(uuid4())), actor)
        barrier = Barrier(2, timeout=10)
        def submit():
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait()
                try:
                    record_refund(db, oid, case["id"], RefundWrite(amount="15.00", reference="verified", occurred_at=utcnow().isoformat() + "Z", expected_revision=1, idempotency_key=str(uuid4())), actor)
                    return True
                except HTTPException as error:
                    db.rollback()
                    self.assertEqual(error.status_code, 409, error.detail)
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit), pool.submit(submit)]
            self.assertEqual(sum(f.result(timeout=20) for f in futures), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.sum(RefundRecord.amount)).where(RefundRecord.order_id == oid)), 15)

    def test_two_adjustment_drafts_cannot_overwrite_the_same_inventory_version(self):
        oid, pid, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            drafts = [create_adjustment(db, AdjustmentCreate(kind="damage", reason="实物损坏", items=[{"product_id": pid, "available_delta": -1}], idempotency_key=str(uuid4())), actor) for _ in range(2)]
        barrier = Barrier(2, timeout=10)
        def submit(draft):
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait()
                try:
                    confirm_adjustment(db, draft["id"], actor)
                    return True
                except HTTPException as error:
                    db.rollback()
                    self.assertEqual(error.status_code, 409)
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, draft) for draft in drafts]
            self.assertEqual(sum(f.result(timeout=20) for f in futures), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ProductModel, pid).stock, 7)
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.product_id == pid, Movement.movement_type == "stock_damage")), 1)
