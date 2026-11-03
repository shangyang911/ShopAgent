"""Cross-module acceptance tests use an isolated SQLite database, never application data."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
from io import StringIO
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.operations.dashboard import dashboard_data, midnight
from app.features.operations.inventory import evaluate_alert, inventory_query, inventory_rows
from app.features.operations.migration import initialize_operations
from app.features.operations.models import Inbound, Movement, OrderEvent, OrderFacts, PrintDocument, PrintJob, Receipt, Reservation, Setting, StockAlert, utcnow
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.products.catalog.crud import ProductCRUD
from app.features.products.models import ProductModel
from app.features.shipping.fulfillment.service import save_shipment
from app.features.shipping.fulfillment.schemas import ShipmentWrite
from app.main import app
from test_order_status import OrderFixtures


class CoreOperationsTests(OrderFixtures, unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, expire_on_commit=False) as db:
            self.actor = UserModel(username="actual-admin", password_hash="unused-test-hash")
            db.add(self.actor)
            db.add(Setting(key="data_coverage_start", value=(utcnow() - timedelta(days=90)).isoformat()))
            db.commit()
        def isolated_db():
            with Session(self.engine, expire_on_commit=False, autoflush=False) as db:
                yield db
        app.dependency_overrides[get_db] = isolated_db
        app.dependency_overrides[require_current_user] = lambda: self.actor
        self.client = TestClient(app)  # No lifespan: do not initialize the configured MySQL database.

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def count(self, model):
        with Session(self.engine) as db:
            return db.scalar(select(func.count()).select_from(model))

    def pay(self, order_id):
        return self.client.patch(f"/api/orders/{order_id}/status", json={"status": "paid_pending_shipment", "idempotency_key": "payment-request-123"})

    def stock_values(self, product_id):
        with Session(self.engine) as db:
            row = inventory_rows(db, inventory_query(db, q=str(product_id)), 1, 20)["items"][0]
            return row["available_quantity"], row["reserved_quantity"], row["on_hand_quantity"]

    def test_receipt_idempotency_snapshot_amount_actor_and_cancel_history(self):
        order_id, product_id, _ = self.seed_order()
        self.assertEqual(self.pay(order_id).status_code, 200)
        self.assertEqual(self.pay(order_id).status_code, 200)
        self.assertEqual(self.count(Receipt), 1)
        self.assertEqual(self.stock_values(product_id), (8, 2, 10))
        with Session(self.engine) as db:
            receipt = db.scalar(select(Receipt))
            self.assertEqual(receipt.amount, Decimal("20.00"))
            self.assertEqual(receipt.actor_name_snapshot, "actual-admin")
        response = self.client.patch(f"/api/orders/{order_id}/status", json={"status": "cancelled", "reason": "客户取消", "idempotency_key": "cancel-request-123"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.patch(f"/api/orders/{order_id}/status", json={"status": "cancelled", "reason": "客户取消", "idempotency_key": "cancel-request-123"}).status_code, 200)
        self.assertEqual(self.stock_values(product_id), (10, 0, 10))
        self.assertEqual(self.count(Receipt), 1)
        self.assertEqual(self.count(OrderEvent), 3)

    def test_dispatch_consumes_reservation_and_completion_never_deducts_twice(self):
        order_id, product_id, _ = self.seed_order((3,))
        self.pay(order_id)
        self.assertEqual(self.client.put(f"/api/shipping/orders/{order_id}/shipment", json={
            "carrier_name": "测试快递", "tracking_number": f"TEST{order_id:012d}", "expected_revision": 0,
        }).status_code, 200)
        for state in ("in_delivery", "completed"):
            response = (self.client.post(f"/api/shipping/orders/{order_id}/dispatch", json={"expected_revision": 1, "idempotency_key": "core-dispatch-request"})
                        if state == "in_delivery" else self.client.patch(f"/api/orders/{order_id}/status", json={"status": state}))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.stock_values(product_id), (7, 0, 7))
        detail = self.client.get(f"/api/orders/{order_id}").json()
        self.assertTrue(detail["paid_at"].endswith("Z"))
        self.assertIsNotNone(detail["dispatch_started_at"])
        self.assertIsNotNone(detail["completed_at"])
        self.assertEqual(self.count(Movement), 3)  # opening, reservation, dispatch

    def test_refund_releases_only_unshipped_reservations(self):
        for dispatch in (False, True):
            order_id, product_id, _ = self.seed_order()
            with Session(self.engine) as db:
                crud = OrderCRUD(db)
                crud.update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT)
                if dispatch:
                    save_shipment(db, order_id, ShipmentWrite(carrier_name="测试快递", tracking_number=f"TEST{order_id:012d}", expected_revision=0), self.actor)
                    crud.update_status(order_id, OrderStatus.IN_DELIVERY)
                crud.update_status(order_id, OrderStatus.REFUNDED, self.actor, "原流程退款")
            self.assertEqual(self.stock_values(product_id), (8, 0, 8) if dispatch else (10, 0, 10))
        self.assertEqual(self.count(Receipt), 2)

    def test_order_creation_key_never_reserves_twice_and_rejects_changed_payload(self):
        _, product_id, payload = self.seed_order()
        headers = {"Idempotency-Key": "new-order-request-123"}
        first = self.client.post("/api/orders", json=payload.model_dump(mode="json"), headers=headers)
        second = self.client.post("/api/orders", json=payload.model_dump(mode="json"), headers=headers)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.stock_values(product_id), (6, 4, 10))
        payload.shipping_fee = Decimal("1.00")
        conflict = self.client.post("/api/orders", json=payload.model_dump(mode="json"), headers=headers)
        self.assertEqual(conflict.status_code, 409)

    def test_inbound_merges_lines_confirms_once_and_is_read_only(self):
        _, pid, _ = self.seed_order()
        payload = {"items": [{"product_id": pid, "quantity": 2}, {"product_id": pid, "quantity": 3}], "remark": "到货核对", "idempotency_key": "inbound-request-123"}
        response = self.client.post("/api/inventory/inbounds", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        receipt_id = response.json()["id"]
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(self.client.post("/api/inventory/inbounds", json=payload).json()["id"], receipt_id)
        for _ in range(2):
            self.assertEqual(self.client.post(f"/api/inventory/inbounds/{receipt_id}/confirm").status_code, 200)
        self.assertEqual(self.stock_values(pid), (13, 2, 15))
        self.assertEqual(self.client.put(f"/api/inventory/inbounds/{receipt_id}", json=payload).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/inventory/inbounds/{receipt_id}").status_code, 409)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.movement_type == "inbound")), 1)

    def test_invalid_inbound_rolls_back_entire_document(self):
        _, pid, _ = self.seed_order()
        response = self.client.post("/api/inventory/inbounds", json={"items": [{"product_id": pid, "quantity": 2}, {"product_id": 999999, "quantity": 1}], "idempotency_key": "bad-inbound-request"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.count(Inbound), 0)
        self.assertEqual(self.stock_values(pid), (8, 2, 10))

    def test_inbound_distinct_product_limit_and_merged_quantity_validation(self):
        _, pid, _ = self.seed_order()
        response = self.client.post('/api/inventory/inbounds', json={
            'items': [{'product_id': pid, 'quantity': 1}] * 101,
            'idempotency_key': 'inbound-many-duplicate-lines'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['items'][0]['quantity'], 101)
        before = self.count(Inbound)
        for items in ([{'product_id': pid, 'quantity': 1000000}] * 2,
                      [{'product_id': index + 1, 'quantity': 1} for index in range(101)]):
            response = self.client.post('/api/inventory/inbounds', json={
                'items': items, 'idempotency_key': 'inbound-invalid-merged-lines'})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(Inbound), before)

    def test_inbound_confirmation_failure_rolls_back_earlier_lines(self):
        _, first, _ = self.seed_order()
        _, second, _ = self.seed_order()
        response = self.client.post('/api/inventory/inbounds', json={
            'items': [{'product_id': first, 'quantity': 2}, {'product_id': second, 'quantity': 2}],
            'idempotency_key': 'inbound-atomic-confirmation'})
        self.assertEqual(response.status_code, 201, response.text)
        receipt_id = response.json()['id']
        with Session(self.engine) as db:
            db.get(ProductModel, second).stock = 2147483647  # Corrupt/limit fixture, not a business edit.
            db.commit()
        before = self.count(Movement)
        response = self.client.post(f'/api/inventory/inbounds/{receipt_id}/confirm')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.stock_values(first), (8, 2, 10))
        self.assertEqual(self.count(Movement), before)
        self.assertEqual(self.client.get(f'/api/inventory/inbounds/{receipt_id}').json()['status'], 'draft')

    def test_csv_exports_all_matching_rows_and_preserves_signed_numbers_and_snapshots(self):
        _, pid, _ = self.seed_order()
        snapshot = '=SUM(1,2)\n带逗号,的商品'
        remark = '@危险公式\n实收核对'
        with Session(self.engine) as db:
            db.get(ProductModel, pid).name = snapshot
            db.commit()
        for index in range(23):
            response = self.client.post('/api/inventory/inbounds', json={
                'items': [{'product_id': pid, 'quantity': 2}], 'remark': remark,
                'idempotency_key': f'csv-export-inbound-{index:03d}'})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.client.post(f"/api/inventory/inbounds/{response.json()['id']}/confirm").status_code, 200)
        with Session(self.engine) as db:
            db.get(ProductModel, pid).name = '已改名的商品'
            db.commit()
        params = {'product_id': pid, 'movement_type': 'inbound', 'actor': self.actor.username}
        listed = self.client.get('/api/inventory/movements', params=params).json()
        self.assertEqual(len(listed['items']), 20)
        response = self.client.get('/api/inventory/movements/export', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('attachment;', response.headers['content-disposition'])
        rows = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), listed['total'])
        self.assertEqual(len(rows), 23)
        self.assertEqual(rows[0]['product_name_snapshot'], "'" + snapshot)
        self.assertEqual(rows[0]['reason'], "'" + remark)
        self.assertEqual(rows[0]['available_delta'], '2')
        self.assertTrue(rows[0]['occurred_at'].endswith('Z'))
        reserved = self.client.get('/api/inventory/movements/export', params={'movement_type': 'order_reserve'})
        self.assertEqual(list(csv.DictReader(StringIO(reserved.content.decode('utf-8-sig'))))[0]['available_delta'], '-2')
        receipt_export = self.client.get('/api/inventory/inbounds/export')
        self.assertEqual(receipt_export.status_code, 200, receipt_export.text)
        receipt_rows = list(csv.DictReader(StringIO(receipt_export.content.decode('utf-8-sig'))))
        self.assertEqual(len(receipt_rows), 23)
        self.assertEqual(receipt_rows[0]['product_name_snapshot'], "'" + snapshot)
        self.assertNotIn('creation_hash', receipt_rows[0])
        self.assertNotIn('creation_key', receipt_rows[0])

    def test_csv_time_validation_matches_list_and_export_requires_authentication(self):
        for path in ('/api/inventory/movements', '/api/inventory/movements/export'):
            for params in ({'start_at': '2026-10-01T00:00:00'},
                           {'start_at': '2026-10-02T00:00:00Z', 'end_at': '2026-10-01T00:00:00Z'}):
                self.assertEqual(self.client.get(path, params=params).status_code, 422)
        app.dependency_overrides.pop(require_current_user)
        for path in ('/api/inventory/movements/export', '/api/inventory/inbounds/export'):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_deleted_product_remains_identifiable_in_confirmed_inbound_and_movements(self):
        order_id, pid, _ = self.seed_order()
        response = self.client.patch(f'/api/orders/{order_id}/status', json={'status': 'cancelled', 'reason': '验收历史快照'})
        self.assertEqual(response.status_code, 200, response.text)
        draft = self.client.post('/api/inventory/inbounds', json={
            'items': [{'product_id': pid, 'quantity': 2}], 'idempotency_key': 'deleted-product-snapshot'}).json()
        self.client.post(f"/api/inventory/inbounds/{draft['id']}/confirm")
        with Session(self.engine) as db:
            self.assertTrue(ProductCRUD(db).delete_product(pid))
        detail = self.client.get(f"/api/inventory/inbounds/{draft['id']}").json()
        self.assertEqual(detail['items'][0]['product_name_snapshot'], 'Regression product')
        history = self.client.get('/api/inventory/movements', params={'product_id': pid}).json()
        self.assertEqual(history['total'], 4)
        self.assertTrue(all(row['product_name_snapshot'] == 'Regression product' for row in history['items']))

    def test_alert_lifecycle_threshold_zero_ack_and_monitor_switch(self):
        _, pid, _ = self.seed_order((5,))  # 10 -> 5, threshold defaults to 5.
        with Session(self.engine) as db:
            alert = db.scalar(select(StockAlert).where(StockAlert.active_product_id == pid))
            alert_id = alert.id
            self.assertEqual(alert.severity, "low_stock")
        self.client.post(f"/api/inventory/alerts/{alert_id}/acknowledge")
        with Session(self.engine) as db:
            product = db.get(ProductModel, pid)
            product.stock = 0; evaluate_alert(db, product); db.commit()
            self.assertEqual(db.scalar(select(StockAlert).where(StockAlert.active_product_id == pid)).id, alert_id)
            self.assertEqual(db.get(StockAlert, alert_id).severity, "out_of_stock")
            product.stock = 5; evaluate_alert(db, product); db.commit()
            self.assertEqual(db.get(StockAlert, alert_id).alert_status, "active")
            product.stock = 6; evaluate_alert(db, product); db.commit()
            self.assertEqual(db.get(StockAlert, alert_id).alert_status, "resolved")
            product.stock = 0; evaluate_alert(db, product); db.commit()
            self.assertNotEqual(db.scalar(select(StockAlert).where(StockAlert.active_product_id == pid)).id, alert_id)
        response = self.client.put(f"/api/inventory/products/{pid}/rule", json={"monitor_enabled": True, "threshold_override": 0})
        self.assertEqual(response.json()["effective_threshold"], 0)
        self.client.put(f"/api/inventory/products/{pid}/rule", json={"monitor_enabled": False, "threshold_override": 0})
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(StockAlert).where(StockAlert.active_product_id == pid)))
            last = db.scalar(select(StockAlert).order_by(StockAlert.id.desc()))
            self.assertEqual(last.close_reason, "monitor_disabled")

    def test_print_request_is_immutable_idempotent_and_never_changes_inventory(self):
        order_id, pid, _ = self.seed_order()
        self.pay(order_id)
        before = self.stock_values(pid)
        payload = {"order_ids": [order_id], "idempotency_key": "print-request-123", "picking_note": "<script>alert(1)</script>"}
        response = self.client.post("/api/order-operations/print-jobs", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        job = response.json(); doc = job["documents"][0]
        self.assertEqual(self.client.post("/api/order-operations/print-jobs", json=payload).json()["id"], job["id"])
        self.assertEqual(self.stock_values(pid), before)
        self.assertEqual(self.count(PrintJob), 1)
        self.client.post(f"/api/order-operations/orders/{order_id}/notes", json={"content": "不应自动印出的内部讨论", "idempotency_key": "internal-note-123"})
        html = self.client.get(f"/api/order-operations/print-jobs/{job['id']}/html").text
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("不应自动印出", html)
        self.assertNotIn("应收（含运费）", html)
        reprint = self.client.post("/api/order-operations/print-jobs", json={"order_ids": [order_id], "source_documents": {str(order_id): doc["id"]}, "reprint_reason": "打印机无纸", "idempotency_key": "reprint-request-123"})
        self.assertEqual(reprint.status_code, 201, reprint.text)
        self.assertEqual(reprint.json()["documents"][0]["document_hash"], doc["document_hash"])
        self.assertEqual(reprint.json()["documents"][0]["attempt_no"], 2)

    def test_print_batch_conflict_is_atomic_and_cancelled_document_cannot_reprint(self):
        paid, _, _ = self.seed_order()
        unpaid, _, _ = self.seed_order()
        self.pay(paid)
        response = self.client.post("/api/order-operations/print-jobs", json={"order_ids": [paid, unpaid], "idempotency_key": "batch-conflict-123"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("整批未生成", response.text)
        self.assertEqual(self.count(PrintJob), 0)
        first = self.client.post("/api/order-operations/print-jobs", json={"order_ids": [paid], "idempotency_key": "first-print-123"}).json()
        self.client.patch(f"/api/orders/{paid}/status", json={"status": "cancelled", "reason": "取消"})
        response = self.client.post("/api/order-operations/print-jobs", json={"order_ids": [paid], "source_documents": {str(paid): first["documents"][0]["id"]}, "reprint_reason": "补打", "idempotency_key": "invalid-reprint-123"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.count(PrintJob), 1)

    def test_business_dates_drill_and_trend_match_decimal_receipts(self):
        order_id, _, _ = self.seed_order()
        day = (utcnow() - timedelta(days=4)).date()
        created_at, paid_at, completed_at = midnight(day) + timedelta(hours=23), midnight(day + timedelta(days=1)) + timedelta(hours=10), midnight(day + timedelta(days=2)) + timedelta(hours=10)
        with Session(self.engine) as db:
            facts = db.get(OrderFacts, order_id); facts.created_at = created_at
            db.get(OrderModel, order_id).created_at = created_at; db.commit()
            with patch("app.features.orders.crud.utcnow", return_value=paid_at):
                OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, self.actor)
            save_shipment(db, order_id, ShipmentWrite(carrier_name="测试快递", tracking_number=f"TEST{order_id:012d}", expected_revision=0), self.actor)
            OrderCRUD(db).update_status(order_id, OrderStatus.IN_DELIVERY)
            with patch("app.features.orders.crud.utcnow", return_value=completed_at):
                OrderCRUD(db).update_status(order_id, OrderStatus.COMPLETED, self.actor)
            result = dashboard_data(db, day, day + timedelta(days=3))
        self.assertEqual(result["trend"][0]["created_order_count"], 1)
        self.assertEqual(result["trend"][0]["confirmed_receipt_amount"], "0.00")
        self.assertEqual(result["trend"][1]["confirmed_receipt_amount"], "20.00")
        self.assertEqual(result["trend"][2]["completed_order_count"], 1)
        self.assertEqual(result["metrics"]["confirmed_receipt_amount"]["value"], "20.00")
        point = result["trend"][1]
        detail = self.client.get("/api/orders", params={"time_field": "paid_at", "start_at": point["start_at"], "end_at": point["end_at"], "as_of": result["as_of"]}).json()
        self.assertEqual(detail["total"], 1)
        self.assertEqual(sum(Decimal(item["total"]) for item in detail["items"]), Decimal("20.00"))
        self.assertEqual(detail["items"][0]["id"], order_id)

    def test_missing_history_is_unknown_and_migration_does_not_deduct_again(self):
        order_id, pid, _ = self.seed_order()
        with Session(self.engine) as db:
            for cls in (OrderFacts, OrderEvent, Reservation, Movement, Setting):
                db.query(cls).delete()
            db.commit()
            before = db.get(ProductModel, pid).stock
            initialize_operations(db)
            initialize_operations(db)
            self.assertEqual(db.get(ProductModel, pid).stock, before)
            self.assertIsNone(db.get(OrderFacts, order_id).paid_at)
            self.assertIsNone(db.get(OrderFacts, order_id).created_at)
            self.assertEqual(db.scalar(select(func.count()).select_from(Reservation)), 1)
            self.assertEqual(db.scalar(select(OrderEvent)).event_type, "historical_import")
            yesterday = (utcnow() - timedelta(days=1)).date()
            result = dashboard_data(db, yesterday, yesterday + timedelta(days=1))
            self.assertIsNone(result["metrics"]["confirmed_receipt_amount"]["value"])
            self.assertIsNone(result["trend"][0]["confirmed_receipt_amount"])

    def test_combined_filters_amount_boundaries_notes_and_escaping(self):
        order_id, pid, _ = self.seed_order()
        self.pay(order_id)
        result = self.client.get("/api/orders", params={"keyword": "Regression", "min_total": "20.00", "max_total": "20.00", "statuses": ["paid_pending_shipment", "completed"], "print_request_status": "never"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["total"], 1)
        self.assertEqual(self.client.get("/api/orders", params={"keyword": "%"}).json()["total"], 0)
        self.assertEqual(self.client.get("/api/orders", params={"min_total": "30", "max_total": "20"}).status_code, 422)
        self.assertEqual(self.client.get("/api/orders", params={"start_at": "2026-01-01T00:00:00"}).status_code, 422)
        payload = {"content": "客服已核对", "idempotency_key": "note-unique-request"}
        self.client.post(f"/api/order-operations/orders/{order_id}/notes", json=payload)
        self.client.post(f"/api/order-operations/orders/{order_id}/notes", json=payload)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(OrderEvent).where(OrderEvent.event_type == "note_added")), 1)
            with self.assertRaises(Exception):
                ProductCRUD(db).delete_product(pid)

    def test_unauthenticated_inventory_dashboard_and_print_access_denied(self):
        app.dependency_overrides.pop(require_current_user)
        for url in ("/api/inventory", "/api/dashboard?start_date=2026-10-01&end_date=2026-10-02", "/api/order-operations/print-jobs/1/html"):
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_zero_baseline_unknown_and_zero_are_distinct(self):
        day = utcnow().replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
        with Session(self.engine) as db:
            result = dashboard_data(db, day, day + timedelta(days=1))
            self.assertEqual(result['metrics']['confirmed_receipt_amount']['value'], '0.00')
            self.assertEqual(result['metrics']['confirmed_receipt_amount']['change_label'], '持平')
            self.assertIsNone(result['metrics']['average_confirmed_order_amount']['value'])
        order_id, _, _ = self.seed_order()
        self.pay(order_id)
        with Session(self.engine) as db:
            result = dashboard_data(db, day, day + timedelta(days=1))
            self.assertEqual(result['metrics']['confirmed_receipt_amount']['change_label'], '新增')

    def test_receipt_commit_failure_rolls_back_all_business_facts(self):
        order_id, pid, _ = self.seed_order()
        with Session(self.engine) as db:
            with patch.object(db, 'commit', side_effect=RuntimeError('commit failed')):
                with self.assertRaises(RuntimeError):
                    OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, self.actor)
            db.rollback()
        self.assertEqual(self.count(Receipt), 0)
        self.assertEqual(self.count(OrderEvent), 1)
        self.assert_state(order_id, pid, OrderStatus.PENDING_PAYMENT, 8)

    def test_opening_inventory_is_audited_and_direct_stock_edits_are_rejected(self):
        from fastapi import HTTPException
        _, pid, _ = self.seed_order()
        with Session(self.engine) as db:
            source = db.get(ProductModel, pid)
            payload = {column.name: getattr(source, column.name) for column in ProductModel.__table__.columns if column.name != 'id'}
            payload['name'] = '新商品期初库存'
            created = ProductCRUD(db).create_product(payload, self.actor)
            movement = db.scalar(select(Movement).where(Movement.product_id == created.id))
            self.assertEqual(movement.movement_type, 'opening')
            self.assertEqual(movement.actor_id, self.actor.id)
            with self.assertRaises(HTTPException) as context:
                ProductCRUD(db).update_product(created.id, {'stock': 999})
            self.assertEqual(context.exception.status_code, 409)

    def test_print_batch_limit_and_individual_document_history(self):
        self.assertEqual(self.client.post('/api/order-operations/print-jobs', json={
            'order_ids': list(range(1, 52)), 'idempotency_key': 'too-many-orders-123'}).status_code, 422)
        order_ids = [self.seed_order()[0] for _ in range(2)]
        with Session(self.engine) as db:
            for order_id in order_ids:
                OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, self.actor)
        job = self.client.post('/api/order-operations/print-jobs', json={
            'order_ids': order_ids, 'idempotency_key': 'two-document-batch'}).json()
        document = job['documents'][0]
        html = self.client.get(f"/api/order-operations/print-jobs/{job['id']}/html", params={'document_id': document['id']}).text
        self.assertEqual(html.count('<template class="document-source">'), 1)
        self.assertEqual(self.client.get(f"/api/order-operations/print-jobs/{job['id']}/html?document_id=999999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
