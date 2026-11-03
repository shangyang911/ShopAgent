"""Order status regressions; optional MySQL tests use a newly created disposable database."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.features.customers.models import CustomerAddressModel, CustomerModel
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.orders.schemas import OrderCreate
from app.features.products.models import ProductModel


class OrderFixtures:
    def seed_order(self, quantities=(2,)):
        with Session(self.engine, expire_on_commit=False) as db:
            customer = CustomerModel(name="Regression customer", phone="10000")
            db.add(customer)
            db.flush()
            address = CustomerAddressModel(
                customer_id=customer.id, recipient_name="Test", phone="10000",
                country="CN", province="P", city="C", district="D", detail="Test street",
            )
            product = ProductModel(
                name="Regression product", category="Test", price=Decimal("10.00"),
                stock=10, status="Active", image_url="/test.png", color="red",
                rating=4, reviews=0, shipping="Standard",
            )
            db.add_all([address, product])
            db.commit()
            payload = OrderCreate(
                customer_id=customer.id, address_id=address.id,
                items=[{"product_id": product.id, "quantity": quantity} for quantity in quantities],
            )
            order, error = OrderCRUD(db).create_order(payload)
            self.assertIsNone(error)
            return order.id, product.id, payload

    def assert_state(self, order_id, product_id, status, stock):
        with Session(self.engine) as db:
            self.assertEqual(db.get(OrderModel, order_id).status, status)
            self.assertEqual(db.get(ProductModel, product_id).stock, stock)


class OrderStatusTests(OrderFixtures, unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_stale_order_cannot_be_paid_after_cancellation(self):
        order_id, product_id, _ = self.seed_order()
        with Session(self.engine, expire_on_commit=False) as stale, Session(self.engine) as fresh:
            snapshot = stale.get(OrderModel, order_id)
            self.assertEqual(snapshot.status, OrderStatus.PENDING_PAYMENT)
            _, error = OrderCRUD(fresh).update_status(order_id, OrderStatus.CANCELLED)
            self.assertIsNone(error)
            row, error = OrderCRUD(stale).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT)
            self.assertIsNotNone(error)
            self.assertEqual(row.status, OrderStatus.CANCELLED)
        self.assert_state(order_id, product_id, OrderStatus.CANCELLED, 10)

    def test_duplicate_cancellation_only_restores_stock_once(self):
        order_id, product_id, _ = self.seed_order((2, 3))
        with Session(self.engine) as db:
            _, error = OrderCRUD(db).update_status(order_id, OrderStatus.CANCELLED)
            self.assertIsNone(error)
            _, error = OrderCRUD(db).update_status(order_id, OrderStatus.CANCELLED)
            self.assertIsNotNone(error)
        self.assert_state(order_id, product_id, OrderStatus.CANCELLED, 10)

    def test_cancellation_does_not_overwrite_another_reservation(self):
        order_id, product_id, payload = self.seed_order()
        with Session(self.engine, expire_on_commit=False) as stale, Session(self.engine) as other:
            snapshot = stale.get(ProductModel, product_id)
            self.assertEqual(snapshot.stock, 8)
            second, error = OrderCRUD(other).create_order(payload)
            self.assertIsNone(error)
            second_id = second.id
            _, error = OrderCRUD(stale).update_status(order_id, OrderStatus.CANCELLED)
            self.assertIsNone(error)
        self.assert_state(order_id, product_id, OrderStatus.CANCELLED, 8)
        self.assert_state(second_id, product_id, OrderStatus.PENDING_PAYMENT, 8)

    def test_stock_and_status_rollback_together(self):
        order_id, product_id, _ = self.seed_order()
        from unittest.mock import patch
        with Session(self.engine) as db:
            with patch.object(db, "commit", side_effect=RuntimeError("commit failed")):
                with self.assertRaises(RuntimeError):
                    OrderCRUD(db).update_status(order_id, OrderStatus.CANCELLED)
            db.rollback()
        self.assert_state(order_id, product_id, OrderStatus.PENDING_PAYMENT, 8)


@unittest.skipUnless(os.getenv("RUN_MYSQL_TESTS") == "1", "Set RUN_MYSQL_TESTS=1 for isolated MySQL tests")
class MySQLConcurrencyTests(OrderFixtures, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy.engine import make_url
        from app.core.config import DATABASE_URL
        url = make_url(DATABASE_URL)
        if url.get_backend_name() != "mysql":
            raise unittest.SkipTest("DATABASE_URL must point to MySQL")
        cls.database_name = "bugfix_test_" + uuid4().hex
        cls.admin_engine = create_engine(url.set(database=None), isolation_level="AUTOCOMMIT")
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE `{cls.database_name}`"))
        cls.addClassCleanup(cls.drop_test_database)
        cls.engine = create_engine(url.set(database=cls.database_name), pool_pre_ping=True)
        cls.addClassCleanup(cls.engine.dispose)
        Base.metadata.create_all(cls.engine)

    @classmethod
    def drop_test_database(cls):
        # Only the random database successfully created by this test run is removed.
        assert cls.database_name.startswith("bugfix_test_") and len(cls.database_name) == 44
        try:
            with cls.admin_engine.connect() as connection:
                connection.execute(text(f"DROP DATABASE `{cls.database_name}`"))
        finally:
            cls.admin_engine.dispose()

    def cancel_concurrently(self, order_ids, product_id):
        barrier = Barrier(2, timeout=10)

        def cancel(order_id):
            with Session(self.engine, expire_on_commit=False) as db:
                # Keep stale identities alive, as overlapping requests can do.
                order = db.get(OrderModel, order_id)
                product = db.get(ProductModel, product_id)
                self.assertIsNotNone(order)
                self.assertIsNotNone(product)
                barrier.wait()
                row, error = OrderCRUD(db).update_status(order_id, OrderStatus.CANCELLED)
                return row.status, error

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cancel, order_id) for order_id in order_ids]
            return [future.result(timeout=15) for future in futures]

    def test_simultaneous_cancellations_accept_only_one(self):
        order_id, product_id, _ = self.seed_order()
        results = self.cancel_concurrently([order_id, order_id], product_id)
        self.assertEqual(sum(error is None for _, error in results), 1)
        self.assert_state(order_id, product_id, OrderStatus.CANCELLED, 10)

    def test_simultaneous_handover_retries_dispatch_stock_once(self):
        from app.features.shipping.fulfillment.service import save_shipment
        from app.features.shipping.fulfillment.schemas import ShipmentWrite
        from app.features.auth.models import UserModel
        from app.features.operations.models import Movement, OrderEvent
        from sqlalchemy import func, select
        order_id, product_id, _ = self.seed_order()
        with Session(self.engine) as db:
            actor = UserModel(username="handover-admin", password_hash="unused")
            db.add(actor)
            db.commit()
            OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            save_shipment(db, order_id, ShipmentWrite(carrier_name="测试快递", tracking_number=f"TEST{order_id:012d}", expected_revision=0), actor)
        barrier = Barrier(2, timeout=10)
        def dispatch():
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait()
                row, error = OrderCRUD(db).update_status(order_id, OrderStatus.IN_DELIVERY,
                    idempotency_key=f"concurrent-handover-{order_id}", shipment_revision=1)
                return row.status, error
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=15) for future in [pool.submit(dispatch), pool.submit(dispatch)]]
        self.assertTrue(all(error is None for _, error in results), results)
        self.assert_state(order_id, product_id, OrderStatus.IN_DELIVERY, 8)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.reference_type == "order", Movement.reference_id == order_id, Movement.movement_type == "dispatch")), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == order_id, OrderEvent.event_type == "in_delivery")), 1)

    def test_simultaneous_tracking_sync_claims_only_one_external_query(self):
        import asyncio
        from unittest.mock import patch, AsyncMock
        from app.features.auth.models import UserModel
        from app.features.shipping.fulfillment.service import save_shipment
        from app.features.shipping.fulfillment.schemas import ShipmentWrite
        from app.features.shipping.tracking.service import sync_tracking
        from app.features.shipping.tracking.provider import Kuaidi100Provider, parse_result
        from app.features.shipping.tracking.models import TrackingEventModel
        from app.features.shipping.tracking.settings import TrackingSettings
        from sqlalchemy import func, select
        order_id, _, _ = self.seed_order()
        number = f"TEST{order_id:012d}"
        with Session(self.engine) as db:
            actor = UserModel(username="tracking-concurrency-admin", password_hash="unused")
            db.add(actor)
            db.commit()
            OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            save_shipment(db, order_id, ShipmentWrite(carrier_name="顺丰速运", tracking_number=number, expected_revision=0), actor)
            OrderCRUD(db).update_status(order_id, OrderStatus.IN_DELIVERY, actor)
        result = parse_result({"com": "shunfeng", "nu": number, "state": "0", "data": [
            {"time": "2026-10-29 10:00:00", "context": "测试隔离响应：运输中"}]}, "shunfeng", number)
        async def query(*args):
            await asyncio.sleep(0.1)
            return result
        barrier = Barrier(2, timeout=10)
        def sync():
            with Session(self.engine, expire_on_commit=False, autoflush=False) as db:
                barrier.wait()
                return asyncio.run(sync_tracking(db, order_id, TrackingSettings(customer="test", key="test")))["sync_result"]
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock(side_effect=query)) as provider:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [future.result(timeout=15) for future in [pool.submit(sync), pool.submit(sync)]]
            self.assertEqual(sorted(results), ["cached", "updated"])
            self.assertEqual(provider.await_count, 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(TrackingEventModel).where(TrackingEventModel.order_id == order_id)), 1)

    def test_different_orders_restore_shared_stock_without_lost_updates(self):
        first_id, product_id, payload = self.seed_order()
        with Session(self.engine) as db:
            second, error = OrderCRUD(db).create_order(payload)
            self.assertIsNone(error)
            second_id = second.id
        results = self.cancel_concurrently([first_id, second_id], product_id)
        self.assertTrue(all(error is None for _, error in results))
        self.assert_state(first_id, product_id, OrderStatus.CANCELLED, 10)
        self.assert_state(second_id, product_id, OrderStatus.CANCELLED, 10)

    def test_simultaneous_receipts_are_one_fact_and_one_event(self):
        from sqlalchemy import func, select
        from app.features.operations.models import Receipt, OrderEvent
        order_id, product_id, _ = self.seed_order()
        barrier = Barrier(2, timeout=10)
        def pay():
            with Session(self.engine, expire_on_commit=False) as db:
                stale = db.get(OrderModel, order_id)
                barrier.wait()
                row, error = OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT)
                return row.status, error
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(pay) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertTrue(all(error is None for _, error in results), results)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Receipt).where(Receipt.order_id == order_id)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == order_id)), 2)
        self.assert_state(order_id, product_id, OrderStatus.PAID_PENDING_SHIPMENT, 8)

    def test_creation_retry_after_final_units_were_reserved_returns_same_order(self):
        _, product_id, payload = self.seed_order((5,))
        barrier = Barrier(2, timeout=10)
        request_key = uuid4().hex
        def create():
            with Session(self.engine, expire_on_commit=False) as db:
                stale = db.get(ProductModel, product_id)
                barrier.wait()
                row, error = OrderCRUD(db).create_order(payload, idempotency_key=request_key)
                return row.id if row else None, error
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertTrue(all(error is None for _, error in results), results)
        self.assertEqual(results[0][0], results[1][0])
        self.assert_state(results[0][0], product_id, OrderStatus.PENDING_PAYMENT, 0)

    def test_simultaneous_inbound_confirmations_change_stock_once(self):
        from types import SimpleNamespace
        from app.features.operations.inventory_router import confirm_inbound
        from app.features.operations.models import Inbound, InboundLine
        _, product_id, _ = self.seed_order()
        with Session(self.engine) as db:
            row = Inbound(receipt_number='IN-' + uuid4().hex, creation_key=uuid4().hex,
                          creation_hash='0' * 64, status='draft', remark='并发入库', created_by='test')
            db.add(row); db.flush()
            receipt_id = row.id
            db.add(InboundLine(receipt_id=row.id, product_id=product_id, product_name_snapshot='Test', quantity=5))
            db.commit()
        barrier = Barrier(2, timeout=10)
        def confirm():
            with Session(self.engine, expire_on_commit=False) as db:
                stale = db.get(Inbound, receipt_id)
                barrier.wait()
                return confirm_inbound(receipt_id, db, SimpleNamespace(id=1, username='test'))['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm) for _ in range(2)]
            self.assertEqual([future.result(timeout=15) for future in futures], ['confirmed', 'confirmed'])
        with Session(self.engine) as db:
            self.assertEqual(db.get(ProductModel, product_id).stock, 13)

    def test_different_inbounds_with_shared_products_do_not_lose_quantities(self):
        from types import SimpleNamespace
        from sqlalchemy import func, select
        from app.features.operations.inventory_router import confirm_inbound
        from app.features.operations.models import Inbound, InboundLine, Movement
        _, first, _ = self.seed_order()
        _, second, _ = self.seed_order()
        receipt_ids = []
        with Session(self.engine) as db:
            for quantity in (3, 4):
                row = Inbound(receipt_number='IN-' + uuid4().hex, creation_key=uuid4().hex,
                              creation_hash='0' * 64, status='draft', remark='共享商品并发入库', created_by='test')
                db.add(row)
                db.flush()
                receipt_ids.append(row.id)
                for pid in (second, first):  # Input order must not change product lock ordering.
                    db.add(InboundLine(receipt_id=row.id, product_id=pid,
                                       product_name_snapshot='Test', quantity=quantity))
            db.commit()
        barrier = Barrier(2, timeout=10)
        def confirm(receipt_id):
            with Session(self.engine, expire_on_commit=False) as db:
                stale = [db.get(ProductModel, pid) for pid in (first, second)]
                barrier.wait()
                return confirm_inbound(receipt_id, db, SimpleNamespace(id=1, username='test'))['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm, receipt_id) for receipt_id in receipt_ids]
            self.assertEqual([future.result(timeout=15) for future in futures], ['confirmed', 'confirmed'])
        with Session(self.engine) as db:
            for pid in (first, second):
                self.assertEqual(db.get(ProductModel, pid).stock, 15)
                self.assertEqual(db.scalar(select(func.sum(Movement.available_delta)).where(
                    Movement.product_id == pid, Movement.movement_type == 'inbound')), 7)

    def test_simultaneous_print_retries_return_one_job_document_and_event(self):
        from types import SimpleNamespace
        from sqlalchemy import func, select
        from app.features.operations.models import PrintDocument, PrintJob, OrderEvent
        from app.features.operations.orders_router import PrintPayload, create_print_job
        order_id, _, _ = self.seed_order()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT)
        barrier = Barrier(2, timeout=10)
        key = uuid4().hex
        def print_request():
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait()
                return create_print_job(PrintPayload(order_ids=[order_id], idempotency_key=key), db,
                                        SimpleNamespace(id=1, username='test'))['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(print_request) for _ in range(2)]
            result = [future.result(timeout=15) for future in futures]
        self.assertEqual(result[0], result[1])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(PrintJob).where(PrintJob.idempotency_key == key)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(PrintDocument).where(PrintDocument.order_id == order_id)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == order_id, OrderEvent.event_type == 'print_requested')), 1)


if __name__ == "__main__":
    unittest.main()
