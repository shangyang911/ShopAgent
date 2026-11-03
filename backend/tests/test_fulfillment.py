"""快递一期验收：独立 SQLite 数据库，不访问业务库或高德服务。"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.operations.inventory import inventory_query, inventory_rows
from app.features.operations.models import Movement, OrderEvent, OrderFacts
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.fulfillment.models import ShipmentModel
from app.main import app
from test_order_status import OrderFixtures


class FulfillmentTests(OrderFixtures, unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, expire_on_commit=False) as db:
            self.actor = UserModel(username="shipping-admin", password_hash="unused")
            db.add(self.actor)
            db.commit()
        def isolated_db():
            with Session(self.engine, expire_on_commit=False, autoflush=False) as db:
                yield db
        app.dependency_overrides[get_db] = isolated_db
        app.dependency_overrides[require_current_user] = lambda: self.actor
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def paid_order(self):
        order_id, product_id, _ = self.seed_order()
        with Session(self.engine) as db:
            _, error = OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, self.actor)
            self.assertIsNone(error)
        return order_id, product_id

    def record(self, order_id, **changes):
        return self.client.put(f"/api/shipping/orders/{order_id}/shipment", json={
            "carrier_name": "顺丰速运", "tracking_number": f"SF{order_id:012d}", "expected_revision": 0, **changes,
        })

    def dispatch(self, order_id, revision=1, key="fulfillment-dispatch-123"):
        return self.client.post(f"/api/shipping/orders/{order_id}/dispatch", json={
            "expected_revision": revision, "idempotency_key": key,
        })

    def stock(self, product_id):
        with Session(self.engine) as db:
            row = inventory_rows(db, inventory_query(db, q=str(product_id)), 1, 20)["items"][0]
            return row["available_quantity"], row["reserved_quantity"], row["on_hand_quantity"]

    def event_count(self, order_id, kind):
        with Session(self.engine) as db:
            return db.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == order_id, OrderEvent.event_type == kind))

    def test_recording_does_not_dispatch_and_no_coordinates_are_required(self):
        oid, pid = self.paid_order()
        response = self.record(oid, carrier_name="  顺丰速运  ", tracking_number=f"  SF{oid:012d}  ")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["carrier_name"], "顺丰速运")
        self.assertIsNone(response.json()["dispatched_at"])
        self.assertEqual(self.stock(pid), (8, 2, 10))
        order = self.client.get(f"/api/orders/{oid}").json()
        self.assertIsNone(order["longitude"])
        self.assertEqual(order["status"], "paid_pending_shipment")
        self.assertEqual(order["shipment"]["revision"], 1)

    def test_handover_and_retries_dispatch_once_with_auditable_carrier(self):
        oid, pid = self.paid_order()
        self.assertEqual(self.record(oid).status_code, 200)
        first, retry = self.dispatch(oid), self.dispatch(oid)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(first.json()["status"], "in_delivery")
        self.assertEqual(self.stock(pid), (8, 0, 8))
        self.assertEqual(first.json()["shipment"]["dispatched_by"], "shipping-admin")
        self.assertTrue(first.json()["shipment"]["dispatched_at"].endswith("Z"))
        self.assertEqual(self.event_count(oid, "in_delivery"), 1)
        with Session(self.engine) as db:
            event = db.scalar(select(OrderEvent).where(OrderEvent.order_id == oid, OrderEvent.event_type == "in_delivery"))
            self.assertEqual(event.details["tracking_number"], f"SF{oid:012d}")
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.movement_type == "dispatch")), 1)
        self.assertEqual(self.dispatch(oid, revision=2).status_code, 409)

    def test_missing_shipment_and_old_status_endpoints_cannot_bypass_handover(self):
        oid, pid = self.paid_order()
        self.assertEqual(self.dispatch(oid).status_code, 409)
        self.record(oid)
        for endpoint in (f"/api/orders/{oid}/status", f"/api/shipping/orders/{oid}/status"):
            self.assertEqual(self.client.patch(endpoint, json={"status": "in_delivery"}).status_code, 409)
        self.assertEqual(self.stock(pid), (8, 2, 10))

    def test_edits_check_revision_and_old_handover_does_not_ship_new_waybill(self):
        oid, pid = self.paid_order()
        self.record(oid)
        self.assertEqual(self.record(oid).status_code, 200)  # 丢失响应后的同内容重试。
        self.assertEqual(self.event_count(oid, "shipment_recorded"), 1)
        changed = self.record(oid, expected_revision=1, tracking_number="SF987654321000")
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["revision"], 2)
        self.assertEqual(self.record(oid, expected_revision=1, tracking_number="SF111111111111").status_code, 409)
        self.assertEqual(self.dispatch(oid, revision=1).status_code, 409)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertEqual(self.dispatch(oid, revision=2).status_code, 200)
        self.assertEqual(self.record(oid, expected_revision=2, tracking_number="SF222222222222").status_code, 409)

    def test_same_carrier_waybill_cannot_be_used_for_two_orders(self):
        first, _ = self.paid_order()
        second, _ = self.paid_order()
        self.assertEqual(self.record(first, tracking_number="SF123456789000").status_code, 200)
        self.assertEqual(self.record(second, tracking_number="SF123456789000").status_code, 409)
        self.assertIsNone(self.client.get(f"/api/orders/{second}").json()["shipment"])
        self.assertEqual(self.event_count(second, "shipment_recorded"), 0)

    def test_cancelled_unpaid_and_missing_orders_cannot_register_or_dispatch(self):
        unpaid, pid, _ = self.seed_order()
        self.assertEqual(self.record(unpaid).status_code, 409)
        oid, _ = self.paid_order()
        self.record(oid)
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.CANCELLED, self.actor, "取消")
        self.assertEqual(self.record(oid).status_code, 409)
        self.assertEqual(self.dispatch(oid).status_code, 409)
        self.assertEqual(self.record(99999).status_code, 404)
        self.assertEqual(self.stock(pid), (8, 2, 10))

    def test_historical_backfill_preserves_unknown_handover_and_inventory(self):
        oid, pid = self.paid_order()
        # 模拟接入前已发货但无可靠交接时间的记录，补录不能制造新的出库事实。
        with Session(self.engine) as db:
            db.get(OrderModel, oid).status = OrderStatus.IN_DELIVERY
            db.get(OrderFacts, oid).dispatch_started_at = None
            db.commit()
        before = self.stock(pid)
        self.assertEqual(self.record(oid).status_code, 422)
        response = self.record(oid, remark="根据历史纸质交接单补录")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["historical"])
        self.assertIsNone(response.json()["dispatched_at"])
        self.assertEqual(self.stock(pid), before)
        self.assertEqual(self.event_count(oid, "in_delivery"), 0)

    def test_failed_commit_rolls_back_handover_stock_and_shipment_together(self):
        oid, pid = self.paid_order()
        self.record(oid)
        with Session(self.engine) as db:
            with patch.object(db, "commit", side_effect=RuntimeError("simulated storage failure")):
                with self.assertRaises(RuntimeError):
                    OrderCRUD(db).update_status(oid, OrderStatus.IN_DELIVERY, self.actor,
                        idempotency_key="rollback-dispatch-123", shipment_revision=1)
            db.rollback()
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertIsNone(self.client.get(f"/api/orders/{oid}").json()["shipment"]["dispatched_at"])
        self.assertEqual(self.event_count(oid, "in_delivery"), 0)

    def test_workbench_reads_without_geocoding_and_searches_waybill(self):
        oid, _ = self.paid_order()
        self.record(oid)
        with patch("app.features.shipping.delivery.service.ShippingService._request_amap", side_effect=AssertionError("must not call AMap")):
            response = self.client.get("/api/shipping/workbench", params={"keyword": f"SF{oid:012d}"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["id"], oid)
        self.assertEqual(response.json()["counts"], {"paid_pending_shipment": 1})

    def test_workbench_paginates_and_counts_before_current_status_filter(self):
        first, _ = self.paid_order()
        second, _ = self.paid_order()
        self.record(second)
        self.dispatch(second)
        response = self.client.get("/api/shipping/workbench", params={"page_size": 1, "page": 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(response.json()["items"][0]["id"], second)
        filtered = self.client.get("/api/shipping/workbench", params={"status": "paid_pending_shipment"}).json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["id"], first)
        self.assertEqual(filtered["counts"], {"paid_pending_shipment": 1, "in_delivery": 1})

    def test_invalid_tracking_numbers_and_guest_access_are_rejected(self):
        oid, _ = self.paid_order()
        for tracking in ("123", "SF 123456", "<script>"):
            self.assertEqual(self.record(oid, tracking_number=tracking).status_code, 422)
        app.dependency_overrides.pop(require_current_user)
        self.assertEqual(self.client.get("/api/shipping/workbench").status_code, 401)
        self.assertEqual(self.record(oid).status_code, 401)
        self.assertEqual(self.dispatch(oid).status_code, 401)
