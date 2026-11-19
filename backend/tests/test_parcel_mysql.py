"""可选 MySQL 并发验收；只使用随机创建的隔离数据库。"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from uuid import uuid4
from decimal import Decimal
import asyncio
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.features.auth.models import UserModel
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.shipping.fulfillment.service import save_shipment
from app.features.shipping.parcels.schemas import FreightWrite, ParcelWrite
from app.features.shipping.parcels.models import FreightRecord
from app.features.shipping.parcels.service import write_extra, write_freight
from app.features.shipping.fulfillment.schemas import ShipmentWrite
import test_order_status as fixtures
import test_platform_mysql as concurrency_fixtures


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

    def test_different_orders_can_create_primary_waybills_concurrently(self):
        from app.features.shipping.fulfillment.models import ShipmentModel
        actor = self.actor()
        orders = [self.paid(actor)[0] for _ in range(2)]
        def submit(index):
            with Session(self.engine) as db:
                row = save_shipment(db, orders[index], ShipmentWrite(carrier_name='顺丰速运',
                    tracking_number='MAIN'+uuid4().hex[:20], expected_revision=0), actor)
                return row.order_id
        results = concurrency_fixtures.PlatformMySQLTests.overlapping_missing_key_requests(self, ShipmentModel, 'order_id', submit)
        self.assertEqual(set(results), set(orders))

    def test_different_orders_can_create_label_tasks_concurrently(self):
        from app.features.shipping.labels.service import prepare_label
        from app.features.shipping.labels.models import LabelTask
        from app.features.shipping.labels.schemas import LabelCreate
        from test_parcels import SETTINGS
        actor = self.actor()
        orders = [self.paid(actor)[0] for _ in range(2)]
        def submit(index):
            payload = LabelCreate(carrier_name='顺丰速运', weight='1.000', expected_revision=0, idempotency_key=str(uuid4()))
            with Session(self.engine) as db:
                task, _, code = prepare_label(db, orders[index], 1, payload, actor, SETTINGS)
                self.assertEqual(code, 'shunfeng')
                return task.order_id
        results = concurrency_fixtures.PlatformMySQLTests.overlapping_missing_key_requests(self, LabelTask, 'idempotency_key', submit)
        self.assertEqual(set(results), set(orders))

    def test_different_orders_can_register_freight_concurrently(self):
        actor = self.actor()
        orders = [self.paid(actor)[0] for _ in range(2)]
        with Session(self.engine) as db:
            for oid in orders:
                save_shipment(db, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number='FREIGHT'+uuid4().hex[:20], expected_revision=0), actor)
                _, error = OrderCRUD(db).update_status(oid, OrderStatus.IN_DELIVERY, actor)
                self.assertIsNone(error)
        def submit(index):
            with Session(self.engine) as db:
                return write_freight(db, orders[index], 1, FreightWrite(actual_cost='5.00', source='并发月结', expected_revision=0), actor)['order_id']
        results = concurrency_fixtures.PlatformMySQLTests.overlapping_missing_key_requests(self, FreightRecord, 'order_id', submit)
        self.assertEqual(set(results), set(orders))

    def test_stale_snapshot_cannot_overwrite_a_newer_freight_revision(self):
        actor = self.actor()
        oid, _ = self.paid(actor)
        with Session(self.engine) as db:
            save_shipment(db, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number='FREIGHT'+uuid4().hex[:20], expected_revision=0), actor)
            _, error = OrderCRUD(db).update_status(oid, OrderStatus.IN_DELIVERY, actor)
            self.assertIsNone(error)
            write_freight(db, oid, 1, FreightWrite(actual_cost='5.00', source='初始月结单', expected_revision=0), actor)
        with Session(self.engine) as stale:
            old = stale.get(FreightRecord, (oid, 1))
            self.assertEqual(old.revision, 1)
            with Session(self.engine) as fresh:
                write_freight(fresh, oid, 1, FreightWrite(actual_cost='8.00', source='新月结单', expected_revision=1), actor)
            with self.assertRaises(HTTPException) as raised:
                write_freight(stale, oid, 1, FreightWrite(actual_cost='6.00', source='旧页面修改', expected_revision=1), actor)
            self.assertEqual(raised.exception.status_code, 409)
            stale.rollback()
        with Session(self.engine) as db:
            self.assertEqual(db.get(FreightRecord, (oid, 1)).actual_cost, Decimal('8.00'))

    def test_stale_snapshot_cannot_replace_a_newer_manifest(self):
        actor = self.actor()
        oid, _ = self.paid(actor)
        with Session(self.engine) as db:
            save_shipment(db, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number='MAIN'+uuid4().hex[:20], expected_revision=0), actor)
        with Session(self.engine) as stale:
            from app.features.shipping.fulfillment.models import ShipmentModel
            old = stale.get(ShipmentModel, oid)
            self.assertEqual(old.revision, 1)
            with Session(self.engine) as fresh:
                write_extra(fresh, oid, 2, ParcelWrite(carrier_name='中通快递', tracking_number='EXTRA'+uuid4().hex[:20], expected_revision=1), actor)
            with self.assertRaises(HTTPException) as raised:
                write_extra(stale, oid, 3, ParcelWrite(carrier_name='中通快递', tracking_number='EXTRA'+uuid4().hex[:20], expected_revision=1), actor)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn('清单', raised.exception.detail)
            stale.rollback()

    def test_stale_snapshot_cannot_edit_a_newer_primary_waybill(self):
        actor = self.actor()
        oid, _ = self.paid(actor)
        from app.features.shipping.fulfillment.models import ShipmentModel
        original = 'MAIN'+uuid4().hex[:20]
        updated = 'MAIN'+uuid4().hex[:20]
        with Session(self.engine) as db:
            save_shipment(db, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number=original, expected_revision=0), actor)
        with Session(self.engine) as stale:
            old = stale.get(ShipmentModel, oid)
            self.assertEqual(old.revision, 1)
            with Session(self.engine) as fresh:
                save_shipment(fresh, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number=updated, expected_revision=1), actor)
            with self.assertRaises(HTTPException) as raised:
                save_shipment(stale, oid, ShipmentWrite(carrier_name='顺丰速运', tracking_number=original+'X', expected_revision=1), actor)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn('更新', raised.exception.detail)
            stale.rollback()

    def test_label_database_retry_never_repeats_the_external_request(self):
        from app.features.shipping.labels import service as labels
        from app.features.shipping.labels.models import LabelTask
        from app.features.shipping.labels.schemas import LabelCreate
        from test_parcels import SETTINGS
        actor = self.actor()
        oid, _ = self.paid(actor)
        result = {'tracking_number': 'SF'+uuid4().hex[:20], 'provider_task_id': 'test-task',
                  'label_url': 'https://api.kuaidi100.com/label/test-task'}
        failure = OperationalError('SELECT', {}, Exception(1213, 'isolated deadlock'))
        finish = labels.finish_task
        calls = []
        def finish_after_deadlock(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise failure
            return finish(*args, **kwargs)
        with Session(self.engine) as db:
            with patch.object(labels.LabelProvider, 'create', new=AsyncMock(return_value=result)) as provider:
                with patch.object(labels, 'finish_task', side_effect=finish_after_deadlock):
                    task = asyncio.run(labels.create_label(db, oid, 1, LabelCreate(carrier_name='顺丰速运', weight='1.000',
                        expected_revision=0, idempotency_key=str(uuid4())), actor, SETTINGS))
            provider.assert_awaited_once()
            self.assertEqual(task['status'], 'issued')
            self.assertEqual(db.get(LabelTask, task['id']).status, 'issued')
        self.assertEqual(len(calls), 2)

    def test_stale_snapshot_cannot_cancel_while_a_label_result_is_unknown(self):
        actor = self.actor()
        oid, pid = self.paid(actor)
        from app.features.shipping.labels.models import LabelTask
        with Session(self.engine) as stale:
            self.assertIsNone(stale.scalar(select(LabelTask.id).where(LabelTask.order_id == oid)))
            with Session(self.engine) as fresh:
                fresh.add(LabelTask(order_id=oid, package_no=1, idempotency_key=str(uuid4()), request_hash='test',
                    carrier_name='顺丰速运', expected_revision=0, status='unknown', created_by=actor.username))
                fresh.commit()
            _, error = OrderCRUD(stale).update_status(oid, OrderStatus.CANCELLED, actor, '尚待核实面单')
            self.assertIsNotNone(error)
            self.assertIn('面单', error)
            stale.rollback()
        self.assert_state(oid, pid, OrderStatus.PAID_PENDING_SHIPMENT, 8)

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
