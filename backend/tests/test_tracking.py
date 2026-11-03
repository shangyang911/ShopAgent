"""二期隔离验收：平台响应仅在测试中模拟，产品不注入演示轨迹。"""
import asyncio
from datetime import timedelta
from hashlib import md5
import json
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx
from fastapi import HTTPException
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

import test_fulfillment as fixtures
from test_order_status import OrderFixtures
from app.features.operations.models import utcnow
from app.features.orders.models import OrderModel, OrderStatus
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.tracking.service import poll_due_shipments, sync_tracking, tracking_snapshot
from app.features.shipping.tracking.models import ShipmentTrackingModel, TrackingEventModel
from app.features.shipping.tracking.provider import Kuaidi100Provider, TrackingProviderError, parse_result
from app.features.shipping.tracking.settings import TrackingSettings

SETTINGS = TrackingSettings(customer="test-customer", key="test-secret")
TEST_TIME = utcnow().replace(microsecond=0)


def payload(number="SF123456789000", state="0", age_hours=1, description="测试隔离响应：快件运输中", empty=False):
    local_time = (TEST_TIME + timedelta(hours=8-age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    return {"com": "shunfeng", "nu": number, "state": state, "data": [] if empty else [
        {"time": local_time, "ftime": local_time, "context": description, "location": "测试物流中心"}]}


class TrackingProviderTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, handler):
        client_type = httpx.AsyncClient
        with patch("app.features.shipping.tracking.provider.httpx.AsyncClient", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw)):
            return await Kuaidi100Provider(SETTINGS).query("shunfeng", "SF123456789000", "13800000000", "上海市徐汇区")

    async def test_signing_encoding_and_shanghai_timezone(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://poll.kuaidi100.com/poll/query.do")
            fields = parse_qs(request.content.decode())
            self.assertEqual(fields["customer"], ["test-customer"])
            param = fields["param"][0]
            self.assertEqual(fields["sign"][0], md5((param+"test-secret"+"test-customer").encode()).hexdigest().upper())
            self.assertEqual(json.loads(param)["phone"], "13800000000")
            return httpx.Response(200, json={"com": "shunfeng", "nu": "SF123456789000", "state": "5", "data": [
                {"time": "2026-10-29 10:30:00", "context": "测试：派送中"}]})
        result = await self.request(handler)
        self.assertEqual(result.status, "out_for_delivery")
        self.assertEqual(result.events[0]["occurred_at"].isoformat(), "2026-10-29T02:30:00")

    async def test_network_non_json_and_provider_errors_are_sanitized(self):
        def refused(request):
            raise httpx.ConnectError("contains test-secret", request=request)
        for handler in (refused, lambda _: httpx.Response(502), lambda _: httpx.Response(200, text="not JSON"),
                        lambda _: httpx.Response(200, json={"result": False, "returnCode": "503", "message": "test-secret"})):
            with self.assertRaises(TrackingProviderError) as caught:
                await self.request(handler)
            self.assertNotIn("test-secret", str(caught.exception))

    def test_wrong_waybill_rejected_and_unknown_time_is_not_fabricated(self):
        with self.assertRaises(TrackingProviderError):
            parse_result(payload(number="OTHER000000"), "shunfeng", "SF123456789000")
        data = payload()
        data["data"][0]["ftime"] = data["data"][0]["time"] = "未知时间"
        result = parse_result(data, "shunfeng", "SF123456789000")
        self.assertIsNone(result.events[0]["occurred_at"])
        data["data"] *= 2
        self.assertEqual(len(parse_result(data, "shunfeng", "SF123456789000").events), 1)

    def test_unknown_and_empty_states_do_not_imply_collection_or_signing(self):
        self.assertEqual(parse_result(payload(state="3", empty=True), "shunfeng", "SF123456789000").status, "unknown")
        self.assertEqual(parse_result(payload(state="999"), "shunfeng", "SF123456789000").status, "unknown")
        with patch.dict("os.environ", {"LOGISTICS_SYNC_INTERVAL_MINUTES": "1"}):
            self.assertEqual(TrackingSettings.from_env().interval_minutes, 30)
        self.assertNotIn("test-secret", repr(SETTINGS))
        self.assertNotIn("key", SETTINGS.public())


class TrackingTests(OrderFixtures, unittest.IsolatedAsyncioTestCase):
    # 复用隔离库/账号搭建，不继承一期测试方法，避免重复统计用例。
    setUp = fixtures.FulfillmentTests.setUp
    tearDown = fixtures.FulfillmentTests.tearDown
    paid_order = fixtures.FulfillmentTests.paid_order
    record = fixtures.FulfillmentTests.record
    dispatch = fixtures.FulfillmentTests.dispatch
    stock = fixtures.FulfillmentTests.stock

    def shipped(self):
        order_id, product_id = self.paid_order()
        self.assertEqual(self.record(order_id).status_code, 200)
        self.assertEqual(self.dispatch(order_id, key=f"tracking-handover-{order_id}").status_code, 200)
        return order_id, product_id

    async def synchronize(self, order_id, *, state="0", age=1, empty=False, failure=None):
        result = parse_result(payload(number=f"SF{order_id:012d}", state=state, age_hours=age, empty=empty), "shunfeng", f"SF{order_id:012d}")
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock(side_effect=failure, return_value=result)) as query:
            with Session(self.engine, expire_on_commit=False, autoflush=False) as db:
                answer = await sync_tracking(db, order_id, SETTINGS)
            return answer, query.await_count

    def release_cooldown(self, order_id):
        with Session(self.engine) as db:
            db.get(ShipmentTrackingModel, order_id).next_sync_at = utcnow() - timedelta(seconds=1)
            db.commit()

    async def test_missing_credentials_never_call_provider_or_create_fake_events(self):
        oid, _ = self.shipped()
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock()) as query:
            with Session(self.engine) as db:
                with self.assertRaises(HTTPException) as caught:
                    await sync_tracking(db, oid, TrackingSettings())
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(tracking_snapshot(db, oid, TrackingSettings())["events"], [])
                self.assertEqual(db.scalar(select(func.count()).select_from(TrackingEventModel)), 0)
            query.assert_not_awaited()

    async def test_success_preserves_order_inventory_and_deduplicates_events(self):
        oid, pid = self.shipped()
        before = self.stock(pid)
        answer, calls = await self.synchronize(oid, state="3")
        self.assertEqual(answer["status"], "signed")
        self.assertEqual(calls, 1)
        self.assertTrue(answer["last_success_at"].endswith("Z"))
        self.assertIsNone(answer["estimated_delivery_at"])
        self.assertEqual(self.stock(pid), before)
        self.assertEqual(self.client.get(f"/api/orders/{oid}").json()["status"], "in_delivery")
        self.release_cooldown(oid)
        await self.synchronize(oid, state="3")
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(TrackingEventModel)), 1)

    async def test_throttle_serves_cache_without_second_platform_request(self):
        oid, _ = self.shipped()
        await self.synchronize(oid)
        answer, calls = await self.synchronize(oid)
        self.assertEqual(answer["sync_result"], "cached")
        self.assertEqual(calls, 0)

    async def test_failed_refresh_preserves_last_success_without_creating_todos(self):
        oid, _ = self.shipped()
        original, _ = await self.synchronize(oid)
        self.release_cooldown(oid)
        result, _ = await self.synchronize(oid, failure=TrackingProviderError("network_error", "连接失败"))
        self.assertEqual(result["last_success_at"], original["last_success_at"])
        self.assertEqual(result["events"], original["events"])
        self.assertTrue(result["stale"])
        self.assertFalse(inspect(self.engine).has_table('logistics_issues'))

    async def test_old_payload_cannot_regress_signed_progress(self):
        oid, _ = self.shipped()
        first, _ = await self.synchronize(oid, state="3", age=1)
        self.release_cooldown(oid)
        second, _ = await self.synchronize(oid, state="0", age=24)
        self.assertEqual(second["status"], "signed")
        self.assertEqual(second["latest_event_at"], first["latest_event_at"])
        self.assertEqual(second["event_total"], 2)

    async def test_empty_response_preserves_previously_saved_undated_event(self):
        oid, _ = self.shipped()
        with Session(self.engine) as db:
            db.add(ShipmentTrackingModel(order_id=oid, status="in_transit", shipment_revision=1,
                latest_description="已有时间待核实的节点", last_success_at=utcnow()))
            db.add(TrackingEventModel(order_id=oid, event_hash="undated-test-event", raw_time="未知时间",
                description="已有时间待核实的节点"))
            db.commit()
        answer, _ = await self.synchronize(oid, empty=True)
        self.assertEqual(answer["status"], "in_transit")
        self.assertEqual(answer["latest_description"], "已有时间待核实的节点")

    async def test_carrier_aliases_do_not_query_the_same_actual_waybill_twice(self):
        first, _ = self.shipped()
        await self.synchronize(first)
        second, _ = self.paid_order()
        # 新履约逻辑在登记阶段就拒绝别名重复；仍验证升级前历史重复记录的同步保护。
        self.assertEqual(self.record(second, carrier_name="顺丰", tracking_number=f"SF{first:012d}").status_code, 409)
        from app.features.shipping.fulfillment.models import ShipmentModel
        with Session(self.engine) as db:
            db.add(ShipmentModel(order_id=second, carrier_name="顺丰", tracking_number=f"SF{first:012d}",
                                 updated_by=self.actor.username, revision=1, historical=False))
            db.commit()
        self.assertEqual(self.dispatch(second, key="alias-order-dispatch").status_code, 200)
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock()) as provider:
            with Session(self.engine, autoflush=False) as db:
                with self.assertRaises(HTTPException) as caught:
                    await sync_tracking(db, second, SETTINGS)
                self.assertEqual(caught.exception.status_code, 409)
            provider.assert_not_awaited()

    async def test_concurrent_requests_share_lease_and_only_one_calls_provider(self):
        oid, _ = self.shipped()
        started, release = asyncio.Event(), asyncio.Event()
        async def query(*args):
            started.set()
            await release.wait()
            return parse_result(payload(number=f"SF{oid:012d}"), "shunfeng", f"SF{oid:012d}")
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock(side_effect=query)) as provider:
            with Session(self.engine, autoflush=False, expire_on_commit=False) as first, Session(self.engine, autoflush=False, expire_on_commit=False) as other:
                task = asyncio.create_task(sync_tracking(first, oid, SETTINGS))
                await started.wait()
                cached = await sync_tracking(other, oid, SETTINGS)
                self.assertEqual(cached["sync_result"], "cached")
                self.assertTrue(cached["syncing"])
                release.set()
                await task
            self.assertEqual(provider.await_count, 1)

    async def test_return_status_does_not_restock_or_create_todos(self):
        oid, pid = self.shipped()
        before = self.stock(pid)
        await self.synchronize(oid, state="6")
        self.assertFalse(inspect(self.engine).has_table('logistics_issues'))
        self.assertEqual(self.stock(pid), before)


    async def test_auto_poll_is_opt_in_and_skips_signed_shipments(self):
        oid, _ = self.shipped()
        await self.synchronize(oid, state="3")
        factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.release_cooldown(oid)
        self.assertEqual(await poll_due_shipments(factory, SETTINGS), 0)
        configured = TrackingSettings(customer="test", key="test", auto_sync=True)
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock()) as query:
            self.assertEqual(await poll_due_shipments(factory, configured), 0)
            query.assert_not_awaited()

    def test_retired_issue_routes_are_unavailable(self):
        for table in ('logistics_issues', 'logistics_issue_actions', 'logistics_issue_parcels'):
            self.assertFalse(inspect(self.engine).has_table(table))
        for path in ("/api/shipping/issues", "/api/shipping/issues/summary", "/api/shipping/issues/1/history"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.patch("/api/shipping/issues/1", json={}).status_code, 404)

    def test_guest_tracking_queries_rejected(self):
        app = fixtures.app
        from app.features.auth.router import require_current_user
        app.dependency_overrides.pop(require_current_user)
        for path in ("/api/shipping/tracking-service", "/api/shipping/orders/1/tracking"):
            self.assertEqual(self.client.get(path).status_code, 401)
