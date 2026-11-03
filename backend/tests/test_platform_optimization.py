"""业务闭环验收：隔离数据库、真实 HTTP 输入和平台替身，不接触业务库。"""
from datetime import timedelta
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import test_fulfillment as fixtures
from test_order_status import OrderFixtures
from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.aftersales.models import AfterSale, RefundRecord
from app.features.operations.models import Movement, utcnow
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.products.models import ProductModel
from app.features.products.catalog.schemas import ProductCreate
from app.features.shipping.tracking.models import ShipmentTrackingModel
from app.features.shipping.tracking.parcel_models import ParcelTrackingModel
from app.features.shipping.tracking.provider import Kuaidi100Provider, parse_result
from app.features.shipping.tracking.service import poll_due_shipments, sync_tracking
from app.features.shipping.tracking.settings import TrackingSettings
from app.main import app


class PlatformOptimizationTests(OrderFixtures, unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.FulfillmentTests.setUp
    tearDown = fixtures.FulfillmentTests.tearDown
    paid_order = fixtures.FulfillmentTests.paid_order
    record = fixtures.FulfillmentTests.record
    dispatch = fixtures.FulfillmentTests.dispatch
    stock = fixtures.FulfillmentTests.stock

    def case(self, oid, amount="20.00", **changes):
        response = self.client.post(f"/api/orders/{oid}/aftersales", json={"kind": "refund", "requested_amount": amount,
            "reason": "客户申请售后", "items": [], "idempotency_key": str(uuid4()), **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def refund(self, oid, case, amount="20.00", **changes):
        return self.client.post(f"/api/orders/{oid}/aftersales/{case['id']}/refunds", json={
            "amount": amount, "reference": "银行退款核实流水-1", "occurred_at": utcnow().isoformat() + "Z",
            "expected_revision": case["revision"], "idempotency_key": str(uuid4()), **changes})

    def adjustment(self, pid, **changes):
        body = {"kind": "count", "reason": "实物核对", "items": [{"product_id": pid, "counted_on_hand": 9}], "idempotency_key": str(uuid4()), **changes}
        response = self.client.post("/api/inventory/adjustments", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_full_refund_cancels_unshipped_fulfillment_and_replay_is_exactly_once(self):
        oid, pid = self.paid_order()
        case = self.case(oid)
        when, key = utcnow().isoformat() + "Z", str(uuid4())
        first = self.refund(oid, case, occurred_at=when, idempotency_key=key)
        retry = self.refund(oid, case, occurred_at=when, idempotency_key=key)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(retry.json(), first.json())
        self.assertEqual(first.json()["status"], "closed")
        self.assertEqual(self.stock(pid), (10, 0, 10))
        self.assertEqual(self.client.get(f"/api/orders/{oid}").json()["status"], "cancelled")
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RefundRecord)), 1)
        changed = self.refund(oid, case, amount="19.00", occurred_at=when, idempotency_key=key)
        self.assertEqual(changed.status_code, 409)

    def test_partial_refund_preserves_in_transit_order_and_rejects_over_refund(self):
        oid, pid = self.paid_order()
        self.record(oid)
        self.assertEqual(self.dispatch(oid).status_code, 200)
        case = self.case(oid, "5.00")
        self.assertEqual(self.refund(oid, case, "6.00").status_code, 409)
        self.assertEqual(self.refund(oid, case, "5.00").status_code, 200)
        self.assertEqual(self.client.get(f"/api/orders/{oid}").json()["status"], "in_delivery")
        self.assertEqual(self.stock(pid), (8, 0, 8))
        snapshot = self.client.get(f"/api/orders/{oid}/aftersales").json()
        self.assertEqual(snapshot["refundable_amount"], "15.00")
        response = self.client.post(f"/api/orders/{oid}/aftersales", json={"kind": "refund", "requested_amount": "16.00", "reason": "重复多退", "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 409)

    def test_return_inspection_restocks_only_accepted_goods_and_cannot_be_overwritten(self):
        oid, pid = self.paid_order()
        self.record(oid)
        self.dispatch(oid)
        item_id = self.client.get(f"/api/orders/{oid}").json()["items"][0]["id"]
        case = self.case(oid, kind="return_refund", items=[{"order_item_id": item_id, "quantity": 2}])
        self.assertEqual(self.refund(oid, case).status_code, 200)
        self.assertEqual(self.stock(pid), (8, 0, 8))  # 单有退款不入库。
        case = self.client.get(f"/api/orders/{oid}/aftersales").json()["items"][0]
        payload = {"expected_revision": case["revision"], "note": "一件合格一件破损", "items": [{"order_item_id": item_id, "accepted_quantity": 1, "damaged_quantity": 1}]}
        url = f"/api/orders/{oid}/aftersales/{case['id']}/inspection"
        first, retry = self.client.post(url, json=payload), self.client.post(url, json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "closed")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(self.stock(pid), (9, 0, 9))
        # 售后作为直接来源的入库流水仍要出现在原订单的关联流水与导出中。
        related = self.client.get("/api/inventory/movements", params={"order_id": oid}).json()
        self.assertEqual(sum(row["movement_type"] == "return_received" for row in related["items"]), 1)
        exported = self.client.get("/api/inventory/movements/export", params={"order_id": oid})
        self.assertIn("return_received", exported.content.decode("utf-8-sig"))
        payload["items"][0].update(accepted_quantity=2, damaged_quantity=0)
        self.assertEqual(self.client.post(url, json=payload).status_code, 409)

    def test_return_quantities_are_bounded_across_cases(self):
        oid, pid = self.paid_order()
        self.record(oid)
        self.dispatch(oid)
        item_id = self.client.get(f"/api/orders/{oid}").json()["items"][0]["id"]
        case = self.case(oid, "5.00", kind="return_refund", items=[{"order_item_id": item_id, "quantity": 2}])
        response = self.client.post(f"/api/orders/{oid}/aftersales/{case['id']}/inspection", json={"expected_revision": 1, "note": "全部损坏", "items": [{"order_item_id": item_id, "accepted_quantity": 0, "damaged_quantity": 2}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.refund(oid, response.json(), "5.00").status_code, 200)
        response = self.client.post(f"/api/orders/{oid}/aftersales", json={"kind": "return_refund", "requested_amount": "5.00", "reason": "再退", "items": [{"order_item_id": item_id, "quantity": 1}], "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 409)

    def test_open_case_blocks_dispatch_and_cancellation_unblocks_it(self):
        oid, pid = self.paid_order()
        self.record(oid)
        case = self.case(oid, "5.00")
        self.assertEqual(self.dispatch(oid).status_code, 409)
        response = self.client.post(f"/api/orders/{oid}/aftersales/{case['id']}/cancel", json={"expected_revision": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.dispatch(oid).status_code, 200)

    def test_refund_failure_rolls_back_funds_inventory_and_fulfillment(self):
        oid, pid = self.paid_order()
        case = self.case(oid)
        with patch("app.features.aftersales.service.add_event", side_effect=RuntimeError("storage failure")):
            with self.assertRaises(RuntimeError):
                self.refund(oid, case)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertEqual(self.client.get(f"/api/orders/{oid}").json()["status"], "paid_pending_shipment")
        self.assertEqual(self.client.get(f"/api/orders/{oid}/aftersales").json()["refunded_amount"], "0.00")

    def test_unverified_payment_future_time_and_legacy_refund_mark_are_rejected(self):
        oid, pid, _ = self.seed_order()
        response = self.client.post(f"/api/orders/{oid}/aftersales", json={"kind": "refund", "requested_amount": "1.00", "reason": "未付款", "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 409)
        oid, _ = self.paid_order()
        case = self.case(oid)
        self.assertEqual(self.refund(oid, case, occurred_at=(utcnow() + timedelta(days=1)).isoformat() + "Z").status_code, 422)
        self.assertEqual(self.refund(oid, case, occurred_at="2026-10-30T12:00:00").status_code, 422)
        self.assertEqual(self.client.patch(f"/api/orders/{oid}/status", json={"status": "refunded", "reason": "没有凭据"}).status_code, 409)

    def test_count_draft_preserves_reservations_and_confirmation_is_idempotent(self):
        oid, pid = self.paid_order()
        row = self.adjustment(pid)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertEqual(row["items"][0]["available_delta"], -1)
        url = f"/api/inventory/adjustments/{row['id']}/confirm"
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.stock(pid), (7, 2, 9))
        self.assertEqual(self.client.post(f"/api/inventory/adjustments/{row['id']}/cancel").status_code, 409)

    def test_count_cannot_consume_reserved_units_and_stale_draft_cannot_overwrite_order(self):
        oid, pid = self.paid_order()
        response = self.client.post("/api/inventory/adjustments", json={"kind": "count", "reason": "实盘不足", "items": [{"product_id": pid, "counted_on_hand": 1}], "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 409)
        row = self.adjustment(pid)
        self.client.patch(f"/api/orders/{oid}/status", json={"status": "cancelled", "reason": "客户取消"})
        self.assertEqual(self.client.post(f"/api/inventory/adjustments/{row['id']}/confirm").status_code, 409)
        self.assertEqual(self.stock(pid), (10, 0, 10))

    def test_multi_product_adjustment_is_atomic_when_one_product_changes(self):
        oid, pid = self.paid_order()
        second_oid, second_pid = self.paid_order()
        row = self.adjustment(pid, kind="damage", items=[{"product_id": pid, "available_delta": -1}, {"product_id": second_pid, "available_delta": -2}])
        self.client.patch(f"/api/orders/{second_oid}/status", json={"status": "cancelled", "reason": "状态变化"})
        self.assertEqual(self.client.post(f"/api/inventory/adjustments/{row['id']}/confirm").status_code, 409)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.movement_type == "stock_damage")), 0)

    def test_inbound_correction_requires_original_confirmed_document(self):
        oid, pid = self.paid_order()
        inbound = self.client.post("/api/inventory/inbounds", json={"remark": "到货", "items": [{"product_id": pid, "quantity": 3}], "idempotency_key": str(uuid4())}).json()
        payload = {"kind": "correction", "reason": "原单多录一件", "source_inbound_id": inbound["id"], "items": [{"product_id": pid, "available_delta": -1}], "idempotency_key": str(uuid4())}
        self.assertEqual(self.client.post("/api/inventory/adjustments", json=payload).status_code, 409)
        self.assertEqual(self.client.post(f"/api/inventory/inbounds/{inbound['id']}/confirm").status_code, 200)
        row = self.client.post("/api/inventory/adjustments", json=payload).json()
        self.assertEqual(self.client.post(f"/api/inventory/adjustments/{row['id']}/confirm").status_code, 200)
        self.assertEqual(self.stock(pid), (10, 2, 12))
        self.assertEqual(self.client.get(f"/api/inventory/inbounds/{inbound['id']}").json()["items"][0]["quantity"], 3)

    def test_product_inventory_risk_follows_rule_and_business_status_is_independent(self):
        oid, pid = self.paid_order()
        with Session(self.engine) as db:
            db.get(ProductModel, pid).status = "Out of stock"  # 模拟旧库标签未随补货更新。
            db.commit()
        self.client.put(f"/api/inventory/products/{pid}/rule", json={"monitor_enabled": True, "threshold_override": 9})
        product = self.client.get("/api/products", params={"status": "Active"}).json()["items"][0]
        self.assertEqual(product["status"], "Active")
        self.assertEqual(product["inventory_status"], "low_stock")
        self.client.put(f"/api/inventory/products/{pid}/rule", json={"monitor_enabled": True, "threshold_override": 0})
        self.assertEqual(self.client.get("/api/products").json()["items"][0]["inventory_status"], "normal")
        data = ProductCreate(name="单规格商品", category="测试", price="5", stock=0, status="Active")
        self.assertEqual(data.color, "")
        self.assertEqual(data.shipping, "")

    def test_new_operations_require_authentication(self):
        oid, pid = self.paid_order()
        app.dependency_overrides.pop(require_current_user)
        for method, path, payload in [("get", f"/api/orders/{oid}/aftersales", None), ("post", f"/api/orders/{oid}/aftersales", {}),
                                     ("get", "/api/inventory/adjustments", None), ("post", "/api/inventory/adjustments", {}),
                                     ("get", f"/api/shipping/orders/{oid}/tracking-packages", None)]:
            response = self.client.request(method, path, json=payload)
            self.assertEqual(response.status_code, 401, response.text)

    async def test_packages_have_independent_events_and_partial_signing(self):
        oid, pid = self.paid_order()
        self.record(oid)
        response = self.client.put(f"/api/shipping/orders/{oid}/packages/2", json={"carrier_name": "顺丰速运", "tracking_number": "SFEXTRA123456", "remark": "二号包裹", "expected_revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.dispatch(oid, revision=2).status_code, 200)
        settings = TrackingSettings(customer="test", key="test")
        main_number = self.client.get(f"/api/orders/{oid}").json()["shipment"]["tracking_number"]
        def result(number, state, description):
            return parse_result({"status": "200", "message": "ok", "com": "shunfeng", "nu": number, "state": state,
                "data": [{"time": "2026-10-30 10:00:00", "context": description}]}, "shunfeng", number)
        with Session(self.engine, expire_on_commit=False) as db:
            with patch.object(Kuaidi100Provider, "query", new=AsyncMock(return_value=result(main_number, "3", "主包裹签收"))):
                await sync_tracking(db, oid, settings)
            with patch.object(Kuaidi100Provider, "query", new=AsyncMock(return_value=result("SFEXTRA123456", "2", "二号包裹异常"))):
                await sync_tracking(db, oid, settings, package_no=2)
        overview = self.client.get(f"/api/shipping/orders/{oid}/tracking-packages").json()
        self.assertEqual(overview["signed_count"], 1)
        self.assertEqual(overview["package_count"], 2)
        listed = self.client.get("/api/shipping/workbench", params={"status": "in_delivery"}).json()["items"][0]
        self.assertEqual(listed["logistics_summary"]["signed_count"], 1)
        self.assertEqual(listed["logistics_summary"]["package_count"], 2)
        self.assertEqual(listed["logistics_summary"]["summary"], "部分包裹显示签收")
        self.assertEqual(overview["items"][0]["events"][0]["description"], "主包裹签收")
        self.assertEqual(overview["items"][1]["events"][0]["description"], "二号包裹异常")
        with Session(self.engine) as db:
            db.get(ParcelTrackingModel, (oid, 2)).next_sync_at = utcnow() - timedelta(minutes=1)
            db.commit()
        # 一号已签收时，后台仍应领取二号包裹；各包裹独立更新签收状态。
        automatic = TrackingSettings(customer="test", key="test", auto_sync=True)
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock(return_value=result("SFEXTRA123456", "3", "二号包裹签收"))) as provider:
            count = await poll_due_shipments(sessionmaker(bind=self.engine, expire_on_commit=False), automatic)
            self.assertEqual(count, 1)
            self.assertEqual(provider.await_count, 1)
        self.assertEqual(self.client.get(f"/api/shipping/orders/{oid}/tracking-packages").json()["signed_count"], 2)
        self.assertEqual(self.stock(pid), (8, 0, 8))
        self.assertEqual(self.client.get(f"/api/orders/{oid}").json()["status"], "in_delivery")

    def test_refund_metrics_use_recorded_occurrence_not_order_status(self):
        from app.features.operations.dashboard import stats
        oid, pid = self.paid_order()
        case = self.case(oid, "5.00")
        self.assertEqual(self.refund(oid, case, "5.00").status_code, 200)
        with Session(self.engine) as db:
            values = stats(db, utcnow() - timedelta(hours=1), utcnow() + timedelta(hours=1))
        self.assertEqual(values["recorded_refund_amount"], Decimal("5.00"))
        self.assertEqual(values["net_confirmed_amount"], Decimal("15.00"))

    def test_completed_order_refunds_appear_in_refunded_view_counts_and_export(self):
        oid, _ = self.paid_order()
        self.record(oid)
        self.dispatch(oid)
        self.assertEqual(self.client.patch(f"/api/orders/{oid}/status", json={"status": "completed"}).status_code, 200)
        case = self.case(oid, "10.00")
        first = self.refund(oid, case, "4.00")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.refund(oid, first.json(), "6.00").status_code, 200)
        # 两笔退款仍只返回一张订单，不改变原来的已完成履约事实。
        result = self.client.get("/api/orders", params={"status": "refunded", "page_size": 1}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["status_counts"]["refunded"], 1)
        self.assertEqual(result["items"][0]["id"], oid)
        self.assertEqual(result["items"][0]["status"], "completed")
        self.assertEqual(Decimal(result["items"][0]["refunded_amount"]), Decimal("10.00"))
        self.assertEqual(self.client.get("/api/orders", params={"statuses": ["completed", "refunded"]}).json()["total"], 1)
        import csv
        from io import StringIO
        export = self.client.get("/api/orders/export", params={"status": "refunded"})
        rows = list(csv.DictReader(StringIO(export.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], str(oid))
        self.assertEqual(Decimal(rows[0]["refunded_amount"]), Decimal("10.00"))

    def test_refund_application_is_pending_until_actual_refund_is_recorded(self):
        oid, _ = self.paid_order()
        case = self.case(oid, "5.00")
        pending = self.client.get("/api/orders", params={"status": "aftersale_pending"}).json()
        self.assertEqual([r["id"] for r in pending["items"]], [oid])
        self.assertTrue(pending["items"][0]["aftersale_pending"])
        self.assertEqual(pending["status_counts"]["aftersale_pending"], 1)
        self.assertEqual(self.client.get("/api/orders", params={"status": "refunded"}).json()["total"], 0)
        self.assertEqual(self.refund(oid, case, "5.00").status_code, 200)
        self.assertEqual(self.client.get("/api/orders", params={"status": "aftersale_pending"}).json()["total"], 0)
        refunded = self.client.get("/api/orders", params={"status": "refunded"}).json()
        self.assertEqual(refunded["total"], 1)
        self.assertFalse(refunded["items"][0]["aftersale_pending"])

    def test_refunded_filter_keeps_legacy_orders_and_respects_keyword(self):
        legacy, _, _ = self.seed_order()
        with Session(self.engine) as db:
            row = db.get(OrderModel, legacy)
            row.status = OrderStatus.REFUNDED
            number = row.order_number
            db.commit()
        oid, _ = self.paid_order()
        case = self.case(oid)
        self.assertEqual(self.refund(oid, case).status_code, 200)
        result = self.client.get("/api/orders", params={"status": "refunded"}).json()
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["status_counts"]["refunded"], 2)
        filtered = self.client.get("/api/orders", params={"status": "refunded", "keyword": number}).json()
        self.assertEqual([row["id"] for row in filtered["items"]], [legacy])
        self.assertEqual(filtered["status_counts"]["refunded"], 1)

    def test_shipping_keeps_dispatched_legacy_refunds_in_their_fulfillment_views(self):
        shipped, _ = self.paid_order()
        self.record(shipped)
        self.dispatch(shipped)
        completed, _ = self.paid_order()
        self.record(completed)
        self.assertEqual(self.dispatch(completed, key=f"legacy-completed-{completed}").status_code, 200)
        self.assertEqual(self.client.patch(f"/api/orders/{completed}/status", json={"status": "completed"}).status_code, 200)
        unshipped, _ = self.paid_order()
        with Session(self.engine) as db:
            for oid in (shipped, completed, unshipped):
                db.get(OrderModel, oid).status = OrderStatus.REFUNDED
            db.commit()
        for view, expected in (("in_delivery", shipped), ("completed", completed)):
            response = self.client.get("/api/shipping/workbench", params={"status": view}).json()
            self.assertEqual(response["total"], 1)
            self.assertEqual(response["counts"][view], 1)
            self.assertEqual(response["items"][0]["id"], expected)
            self.assertEqual(response["items"][0]["status"], "refunded")
        self.assertEqual(self.client.get("/api/shipping/workbench").json()["total"], 2)

    def test_assistant_reports_refund_and_pending_facts_separately_from_fulfillment(self):
        from app.features.agent.service import AgentStore, SearchOrders
        oid, _ = self.paid_order()
        case = self.case(oid, "5.00")
        store = AgentStore(sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False))
        cid = store.create_conversation(self.actor.id)["id"]
        request_id = str(uuid4())
        store.claim(cid, self.actor.id, request_id)
        store.begin_turn(cid, self.actor.id, request_id, "查询售后")
        pending = store.execute_tool(cid, self.actor.id, request_id, "search_orders", SearchOrders(status="aftersale_pending").model_dump())
        self.assertEqual(pending["total"], 1)
        self.assertTrue(pending["items"][0]["aftersale_pending"])
        self.refund(oid, case, "5.00")
        refunded = store.execute_tool(cid, self.actor.id, request_id, "search_orders", SearchOrders(status="refunded").model_dump())
        self.assertEqual(refunded["total"], 1)
        self.assertEqual(refunded["items"][0]["status"], "paid_pending_shipment")
        self.assertEqual(Decimal(refunded["items"][0]["refunded_amount"]), Decimal("5.00"))
