"""三期业务验收，隔离 SQLite；平台通过替身测试，无真实取号或扣费。"""
from datetime import timedelta
from decimal import Decimal
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.auth.router import require_current_user
from app.features.operations.models import Movement, OrderEvent, utcnow
from app.features.shipping.labels.provider import LabelProvider, LabelProviderError, LabelSettings, safe_label_url
from app.features.shipping.labels.models import LabelTask
from app.main import app
import test_fulfillment as fixtures
from test_order_status import OrderFixtures

SETTINGS = LabelSettings(key="test-key", secret="test-secret", sender={"name": "测试寄件人", "mobile": "13800000000", "printAddr": "测试仓库"},
                         accounts={"shunfeng": {"partnerId": "test-account", "tempId": "test-template"}})
RESULT = {"tracking_number": "SF888888888888", "provider_task_id": "test-task", "label_url": "https://api.kuaidi100.com/label/test-task"}


class ParcelTests(OrderFixtures, unittest.TestCase):
    setUp = fixtures.FulfillmentTests.setUp
    tearDown = fixtures.FulfillmentTests.tearDown
    paid_order = fixtures.FulfillmentTests.paid_order
    record = fixtures.FulfillmentTests.record
    dispatch = fixtures.FulfillmentTests.dispatch
    stock = fixtures.FulfillmentTests.stock
    event_count = fixtures.FulfillmentTests.event_count

    def extra(self, oid, number="ZT123456789000", revision=1, no=2, **changes):
        return self.client.put(f"/api/shipping/orders/{oid}/packages/{no}", json={"carrier_name": "中通快递", "tracking_number": number,
                               "expected_revision": revision, **changes})

    def packages(self, oid):
        return self.client.get(f"/api/shipping/orders/{oid}/packages").json()

    def freight(self, oid, no=1, cost="5.20", revision=0, source="月结单 TEST-01"):
        return self.client.put(f"/api/shipping/orders/{oid}/packages/{no}/freight", json={"actual_cost": cost, "source": source, "expected_revision": revision})

    def label(self, oid, no=1, key="test-label-request", revision=0):
        return self.client.post(f"/api/shipping/orders/{oid}/packages/{no}/label", json={"carrier_name": "顺丰速运", "weight": "1.250", "expected_revision": revision, "idempotency_key": key})

    def test_extra_parcel_changes_manifest_and_old_dispatch_cannot_ship(self):
        oid, pid = self.paid_order()
        self.record(oid)
        response = self.extra(oid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)
        self.assertEqual(len(response.json()["items"]), 2)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertEqual(self.dispatch(oid, revision=1).status_code, 409)
        self.assertEqual(self.dispatch(oid, revision=2).status_code, 200)
        self.assertEqual(self.stock(pid), (8, 0, 8))
        with Session(self.engine) as db:
            event = db.scalar(select(OrderEvent).where(OrderEvent.order_id == oid, OrderEvent.event_type == "in_delivery"))
            self.assertEqual(len(event.details["packages"]), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.movement_type == "dispatch")), 1)
        self.assertEqual(self.extra(oid, revision=2).status_code, 409)

    def test_package_requires_primary_and_delete_invalidates_manifest(self):
        oid, _ = self.paid_order()
        self.assertEqual(self.extra(oid).status_code, 409)
        self.record(oid)
        self.extra(oid)
        endpoint = f"/api/shipping/orders/{oid}/packages/2"
        self.assertEqual(self.client.delete(endpoint + "?expected_revision=1").status_code, 409)
        deleted = self.client.delete(endpoint + "?expected_revision=2")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["revision"], 3)
        self.assertEqual(len(deleted.json()["items"]), 1)
        self.assertEqual(self.extra(oid, revision=3).status_code, 200)

    def test_alias_case_and_cross_package_duplicates_rejected(self):
        first, _ = self.paid_order()
        second, _ = self.paid_order()
        self.record(first)
        self.assertEqual(self.record(second, carrier_name="顺丰", tracking_number=f"sf{first:012d}").status_code, 409)
        self.record(second)
        self.assertEqual(self.extra(second, number=f"SF{first:012d}", carrier_name="顺丰").status_code, 409)
        self.assertEqual(self.extra(first).status_code, 200)
        self.assertEqual(self.record(second, expected_revision=1, carrier_name="中通", tracking_number="zt123456789000").status_code, 409)
        self.assertEqual(self.extra(first, number=f"SF{first:012d}", revision=2, carrier_name="顺丰").status_code, 409)

    def test_search_extra_waybill_counts_each_order_once(self):
        oid, _ = self.paid_order()
        self.record(oid)
        self.extra(oid)
        response = self.client.get("/api/shipping/workbench?keyword=ZT123456789000").json()
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["counts"], {"paid_pending_shipment": 1})

    def test_freight_preserves_unknown_zero_and_audited_revisions(self):
        oid, _ = self.paid_order()
        self.record(oid)
        self.extra(oid)
        self.assertEqual(self.freight(oid).status_code, 409)
        self.dispatch(oid, revision=2)
        self.assertEqual(self.freight(oid).status_code, 200)
        self.assertEqual(self.freight(oid).status_code, 200)
        self.assertEqual(self.event_count(oid, "freight_settled"), 1)
        report = self.client.get("/api/shipping/freight-report").json()
        self.assertEqual(report["actual_cost"], "5.20")
        self.assertEqual(report["unsettled_packages"], 1)
        self.assertFalse(report["complete"])
        self.assertEqual(self.freight(oid, cost="6.00").status_code, 409)
        self.assertEqual(self.freight(oid, cost="6.00", revision=1, source="修正：账单折扣").status_code, 200)
        self.assertEqual(self.freight(oid, no=2, cost="0.00", source="免费补贴凭证").status_code, 200)
        report = self.client.get("/api/shipping/freight-report").json()
        self.assertEqual(report["actual_cost"], "6.00")
        self.assertEqual(report["order_count"], 1)
        self.assertEqual(report["settled_packages"], 2)
        self.assertTrue(report["complete"])
        order = self.client.get(f"/api/orders/{oid}").json()
        self.assertEqual(order["total"], "20.00")
        for cost in ("-1", "NaN", "Infinity", "1.234"):
            self.assertEqual(self.freight(oid, cost=cost).status_code, 422)
        self.assertEqual(self.freight(oid, source="  ").status_code, 422)

    def test_report_date_boundaries_and_export_unknown(self):
        oid, _ = self.paid_order()
        self.record(oid)
        self.dispatch(oid)
        self.assertEqual(self.client.get("/api/shipping/freight-report?start_at=2000-01-01T00:00:00").status_code, 422)
        self.assertEqual(self.client.get("/api/shipping/freight-report?start_at=2030-01-01T00:00:00Z&end_at=2020-01-01T00:00:00Z").status_code, 422)
        future = self.client.get("/api/shipping/freight-report?start_at=2090-01-01T00:00:00Z").json()
        self.assertEqual(future["package_count"], 0)
        text = self.client.get("/api/shipping/freight-report/export").text
        self.assertIn("待结算", text)
        self.freight(oid, cost="0.00")
        self.assertIn("0.00,已结算", self.client.get("/api/shipping/freight-report/export").text)

    def test_batch_partial_failure_replay_and_payload_reuse(self):
        first, pid = self.paid_order()
        second, _ = self.paid_order()
        self.record(first)
        payload = {"idempotency_key": "batch-parcel-test", "items": [{"order_id": first, "expected_revision": 1}, {"order_id": second, "expected_revision": 1}]}
        response = self.client.post("/api/shipping/dispatch-batch", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["succeeded"], response.json()["failed"]), (1, 1))
        self.record(second)
        replay = self.client.post("/api/shipping/dispatch-batch", json=payload).json()
        self.assertEqual(replay["succeeded"], 2)
        self.assertEqual(self.event_count(first, "in_delivery"), 1)
        self.assertEqual(self.stock(pid), (8, 0, 8))
        payload["items"][0]["expected_revision"] = 2
        self.assertEqual(self.client.post("/api/shipping/dispatch-batch", json=payload).status_code, 409)
        payload["items"].append(payload["items"][0])
        self.assertEqual(self.client.post("/api/shipping/dispatch-batch", json=payload).status_code, 422)

    def test_missing_label_configuration_does_not_create_task(self):
        oid, _ = self.paid_order()
        with patch.object(LabelSettings, "from_env", return_value=LabelSettings()), patch.object(LabelProvider, "create", new=AsyncMock()) as provider:
            self.assertEqual(self.label(oid).status_code, 503)
            provider.assert_not_awaited()
        self.assertEqual(self.packages(oid)["label_tasks"], [])

    def test_label_is_idempotent_and_does_not_dispatch_or_allow_overwrite(self):
        oid, pid = self.paid_order()
        with patch.object(LabelSettings, "from_env", return_value=SETTINGS), patch.object(LabelProvider, "create", new=AsyncMock(return_value=RESULT)) as provider:
            response = self.label(oid)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "issued")
            self.assertEqual(self.label(oid).json()["id"], response.json()["id"])
            self.assertEqual(provider.await_count, 1)
            self.assertEqual(self.label(oid, key="new-label-key-123").status_code, 409)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertEqual(self.record(oid, expected_revision=1, tracking_number="SF999999999999").status_code, 409)
        self.assertEqual(self.packages(oid)["items"][0]["label"]["label_url"], RESULT["label_url"])
        self.assertEqual(self.dispatch(oid).status_code, 200)

    def test_second_package_label_updates_manifest(self):
        oid, _ = self.paid_order()
        self.record(oid)
        with patch.object(LabelSettings, "from_env", return_value=SETTINGS), patch.object(LabelProvider, "create", new=AsyncMock(return_value=RESULT)):
            response = self.label(oid, no=2, revision=1)
        self.assertEqual(response.json()["status"], "issued", response.text)
        self.assertEqual(self.packages(oid)["revision"], 2)
        self.assertEqual(len(self.packages(oid)["items"]), 2)
        self.assertEqual(self.dispatch(oid, revision=1).status_code, 409)

    def test_batch_print_validates_every_package_and_audits_intent_only_once(self):
        oid, pid = self.paid_order()
        with patch.object(LabelSettings, "from_env", return_value=SETTINGS), patch.object(LabelProvider, "create", new=AsyncMock(return_value=RESULT)):
            self.assertEqual(self.label(oid).json()["status"], "issued")
        body = {"items": [{"order_id": oid, "expected_revision": 1}], "idempotency_key": "label-print-test-key"}
        response = self.client.post('/api/shipping/labels/print-batch', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["label_url"], RESULT["label_url"])
        self.assertEqual(self.client.post('/api/shipping/labels/print-batch', json=body).status_code, 200)
        self.assertEqual(self.event_count(oid, "label_print_requested"), 1)
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.extra(oid)
        self.assertEqual(self.client.post('/api/shipping/labels/print-batch', json=body).status_code, 409)
        body["items"][0]["expected_revision"] = 2
        body["idempotency_key"] = "label-print-manual-package"
        self.assertEqual(self.client.post('/api/shipping/labels/print-batch', json=body).status_code, 409)
        self.assertEqual(self.event_count(oid, "label_print_requested"), 1)

    def test_uncertain_label_blocks_changes_until_audited_resolution(self):
        oid, pid = self.paid_order()
        with patch.object(LabelSettings, "from_env", return_value=SETTINGS), patch.object(LabelProvider, "create", new=AsyncMock(side_effect=LabelProviderError("测试超时"))) as provider:
            task = self.label(oid).json()
            self.assertEqual(task["status"], "unknown")
            self.assertEqual(self.label(oid, key="duplicate-label-attempt").status_code, 409)
            self.assertEqual(self.label(oid).json()["status"], "unknown")
            self.assertEqual(provider.await_count, 1)
        self.assertEqual(self.record(oid).status_code, 409)
        self.assertEqual(self.client.patch(f"/api/orders/{oid}/status", json={"status": "cancelled", "reason": "测试取消"}).status_code, 409)
        invalid = self.client.post(f"/api/shipping/orders/{oid}/label-tasks/{task['id']}/resolve", json={"outcome": "issued", "reason": "测试无效单号", "tracking_number": "bad space", "label_url": RESULT["label_url"]})
        self.assertEqual(invalid.status_code, 422)
        response = self.client.post(f"/api/shipping/orders/{oid}/label-tasks/{task['id']}/resolve", json={"outcome": "issued", "reason": "核对平台任务记录", **{k: RESULT[k] for k in ('tracking_number', 'label_url')}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "issued")
        self.assertEqual(self.stock(pid), (8, 2, 10))
        self.assertEqual(self.event_count(oid, "label_reconciled"), 1)

    def test_rejected_label_can_retry_new_key_and_pending_is_guarded(self):
        oid, _ = self.paid_order()
        with patch.object(LabelSettings, "from_env", return_value=SETTINGS), patch.object(LabelProvider, "create", new=AsyncMock(side_effect=LabelProviderError("测试拒绝", uncertain=False))):
            self.assertEqual(self.label(oid).json()["status"], "failed")
        with patch.object(LabelSettings, "from_env", return_value=SETTINGS), patch.object(LabelProvider, "create", new=AsyncMock(return_value=RESULT)):
            self.assertEqual(self.label(oid, key="new-key-after-rejected").json()["status"], "issued")

    def test_all_new_operations_require_authentication(self):
        oid, _ = self.paid_order()
        app.dependency_overrides.pop(require_current_user)
        self.assertEqual(self.extra(oid).status_code, 401)
        self.assertEqual(self.freight(oid).status_code, 401)
        self.assertEqual(self.label(oid).status_code, 401)
        for path in ("freight-report", "freight-report/export", "label-settings", f"orders/{oid}/packages"):
            self.assertEqual(self.client.get('/api/shipping/' + path).status_code, 401)


class LabelProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_signature_and_idempotency_and_result_mapping(self):
        from hashlib import md5
        from types import SimpleNamespace
        order = SimpleNamespace(recipient_name="收件人", recipient_phone="13812345678", province="省", city="市", district="区", address_detail="街道")
        async def send(request):
            from urllib.parse import parse_qs
            params = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            self.assertEqual(params["sign"], md5((params['param'] + params['t'] + SETTINGS.key + SETTINGS.secret).encode()).hexdigest().upper())
            payload = json.loads(params['param'])
            self.assertFalse(payload["reorder"])
            self.assertEqual(payload["orderId"], "test-reference")
            self.assertEqual(payload["count"], 1)
            return httpx.Response(200, json={"code": 30011, "data": {"kuaidinum": RESULT["tracking_number"], "taskId": RESULT["provider_task_id"], "label": RESULT["label_url"]}})
        real_client = httpx.AsyncClient
        with patch('app.features.shipping.labels.provider.httpx.AsyncClient', side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(send), **kwargs)):
            response = await LabelProvider(SETTINGS).create("shunfeng", order, Decimal('1.25'), "test-reference")
        self.assertEqual(response, RESULT)

    def test_label_link_only_allows_official_short_link(self):
        for url in ("javascript:alert(1)", "https://evil.test/label/1", "https://api.kuaidi100.com@evil.test/label/1"):
            self.assertIsNone(safe_label_url(url))
        self.assertEqual(safe_label_url("http://api.kuaidi100.com/label/1"), "https://api.kuaidi100.com/label/1")
