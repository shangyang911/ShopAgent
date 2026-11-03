"""可选 MySQL 并发验收；只使用随机创建的隔离数据库。"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.features.auth.models import UserModel
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.shipping.fulfillment.service import save_shipment
from app.features.shipping.parcels.schemas import ParcelWrite
from app.features.shipping.parcels.service import write_extra
from app.features.shipping.fulfillment.schemas import ShipmentWrite
import test_order_status as fixtures


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Set RUN_MYSQL_TESTS=1 for isolated MySQL tests')
class ParcelMySQLTests(fixtures.OrderFixtures, unittest.TestCase):
    setUpClass = classmethod(fixtures.MySQLConcurrencyTests.setUpClass.__func__)
    drop_test_database = classmethod(fixtures.MySQLConcurrencyTests.drop_test_database.__func__)

    def actor(self):
        with Session(self.engine, expire_on_commit=False) as db:
            row = UserModel(username='parcel-' + uuid4().hex[:20], password_hash='unused')
            db.add(row)
            db.commit()
            return row

    def paid(self, actor):
        oid, pid, _ = self.seed_order()
        with Session(self.engine) as db:
            _, error = OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            self.assertIsNone(error)
        return oid, pid

    def test_two_aliases_claim_same_waybill_only_once(self):
        actor = self.actor()
        orders = [self.paid(actor)[0] for _ in range(2)]
        barrier = Barrier(2, timeout=10)
        number = 'RACE' + uuid4().hex[:20].upper()
        def record(oid, carrier):
            with Session(self.engine, expire_on_commit=False, autoflush=False) as db:
                barrier.wait()
                try:
                    save_shipment(db, oid, ShipmentWrite(carrier_name=carrier, tracking_number=number, expected_revision=0), actor)
                    return True
                except HTTPException as error:
                    db.rollback()
                    self.assertEqual(error.status_code, 409)
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(record, oid, carrier) for oid, carrier in zip(orders, ('顺丰', '顺丰速运'))]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(results), 1)

    def test_extra_package_and_stale_dispatch_are_mutually_exclusive(self):
        actor = self.actor()
        oid, pid = self.paid(actor)
        with Session(self.engine, autoflush=False) as db:
            save_shipment(db, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number='MAIN' + uuid4().hex[:20], expected_revision=0), actor)
        barrier = Barrier(2, timeout=10)
        def add():
            with Session(self.engine, autoflush=False) as db:
                barrier.wait()
                try:
                    write_extra(db, oid, 2, ParcelWrite(carrier_name='中通快递', tracking_number='EXTRA' + uuid4().hex[:20], expected_revision=1), actor)
                    return True
                except HTTPException as error:
                    db.rollback()
                    self.assertEqual(error.status_code, 409)
                    return False
        def dispatch():
            with Session(self.engine, autoflush=False) as db:
                barrier.wait()
                _, error = OrderCRUD(db).update_status(oid, OrderStatus.IN_DELIVERY, actor,
                    idempotency_key='race-dispatch-' + uuid4().hex, shipment_revision=1)
                if error:
                    db.rollback()
                return error is None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(add), pool.submit(dispatch)]
            added, dispatched = [future.result(timeout=20) for future in futures]
        self.assertEqual(int(added) + int(dispatched), 1)
        self.assert_state(oid, pid, OrderStatus.IN_DELIVERY if dispatched else OrderStatus.PAID_PENDING_SHIPMENT, 8)
