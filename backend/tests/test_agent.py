"""客服回归：真实业务代码 + 隔离 SQLite；百炼使用替身，绝不调用付费接口。"""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import importlib
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app.db.base import Base
from app.db.session import get_db
from app.features.agent.models import AgentAction, AgentConversation, AgentConversationArchive, AgentTicket
from app.features.agent.provider import AgentError, BailianProvider
from app.features.agent.service import AgentStore
from app.features.agent.settings import AgentSettings
from app.features.auth.models import AuthSessionModel, UserModel
from app.features.auth.router import COOKIE_NAME
from app.features.auth.security import hash_session_token
from app.features.customers.models import CustomerAddressModel, CustomerModel
from app.features.operations.models import OrderEvent, utcnow
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.orders.schemas import OrderCreate
from app.features.products.models import ProductModel

agent_routes = importlib.import_module("app.features.agent.router")


class FakeProvider:
    def __init__(self, settings):
        self.settings = settings
        self.recognized_text = "查询订单"
        self.responses = []
        self.calls = []
        self.recordings = []
        self.speech_fails = False
        self.block_speech = False

    def require_key(self):
        BailianProvider.require_key(self)

    async def complete(self, messages, tools):
        self.calls.append(list(messages))
        if self.responses:
            return self.responses.pop(0)
        return {"content": json.dumps({"text": "详细说明，请查看订单页面。", "speech_text": "请查看订单详情。"}, ensure_ascii=False)}

    async def recognize(self, queue, callback):
        recording = bytearray()
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            recording.extend(chunk)
            await callback("正在识别", False)
        self.recordings.append(bytes(recording))
        await callback(self.recognized_text, True)
        return self.recognized_text

    async def synthesize(self, text):
        if self.speech_fails:
            raise AgentError("speech_provider", "服务不可用")
        yield b"\x00\x00" * 20
        if self.block_speech:
            await asyncio.Future()

    async def retrieve(self, query):
        return {"configured": False, "documents": [], "notice": "知识库未配置，不能确认退货规则"}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.store = AgentStore(self.factory)
        self.token, self.other_token = "test-session-one", "test-session-two"
        with self.factory() as db:
            db.add_all([UserModel(id=1, username="admin", password_hash="unused"),
                        UserModel(id=2, username="other", password_hash="unused")])
            db.flush()
            db.add_all([AuthSessionModel(user_id=uid, token_hash=hash_session_token(token),
                                        expires_at=utcnow() + timedelta(days=1))
                        for uid, token in ((1, self.token), (2, self.other_token))])
            db.commit()
        self.conversation_id = self.store.create_conversation(1)["id"]
        self.settings = replace(AgentSettings.from_env(), api_key="test-only-key", knowledge_base_id="", rag_url="")
        self.provider = FakeProvider(self.settings)
        self.app = FastAPI()
        self.app.include_router(agent_routes.router, prefix="/api")

        def db_override():
            with self.factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = db_override
        self.patches = [patch.object(agent_routes, "store", self.store),
                        patch.object(agent_routes, "BailianProvider", return_value=self.provider),
                        patch.object(AgentSettings, "from_env", return_value=self.settings)]
        for item in self.patches:
            item.start()
        self.client = TestClient(self.app)
        self.client.cookies.set(COOKIE_NAME, self.token)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def test_logistics_tool_reads_cached_facts_without_querying_provider(self):
        from unittest.mock import AsyncMock
        from app.features.shipping.fulfillment.service import save_shipment
        from app.features.shipping.fulfillment.schemas import ShipmentWrite
        from app.features.shipping.tracking.models import ShipmentTrackingModel, TrackingEventModel
        from app.features.shipping.tracking.provider import Kuaidi100Provider
        from app.features.shipping.tracking.settings import TrackingSettings
        order_id, _ = self.seed_order()
        now = utcnow()
        with self.factory() as db:
            actor = db.get(UserModel, 1)
            OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            save_shipment(db, order_id, ShipmentWrite(carrier_name="顺丰速运", tracking_number="SFTEST123456789", expected_revision=0), actor)
            OrderCRUD(db).update_status(order_id, OrderStatus.IN_DELIVERY, actor)
            db.add(ShipmentTrackingModel(order_id=order_id, status="in_transit", shipment_revision=1,
                last_success_at=now-timedelta(hours=3), latest_event_at=now-timedelta(hours=5), latest_description="隔离测试：运输中"))
            for index in range(17):
                db.add(TrackingEventModel(order_id=order_id, event_hash=f"test-event-{index}",
                    occurred_at=now-timedelta(hours=index+5), raw_time="", description="测试物流节点", location="测试中心"))
            db.commit()
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        self.store.begin_turn(self.conversation_id, 1, request_id, "帮我查物流")
        with patch.object(Kuaidi100Provider, "query", new=AsyncMock()) as provider, patch.object(
            TrackingSettings, "from_env", return_value=TrackingSettings(customer="fake-customer", key="fake-private-key")):
            result = self.store.execute_tool(self.conversation_id, 1, request_id, "get_logistics", {"order_id": order_id})
            provider.assert_not_awaited()
        self.assertEqual(result["status"], "in_transit")
        self.assertTrue(result["stale"])
        self.assertEqual(len(result["events"]), 16)
        self.assertTrue(result["events_truncated"])
        self.assertIsNone(result["estimated_delivery_at"])
        self.assertIn("不是刚刚", result["notice"])
        self.assertNotIn("fake-private-key", json.dumps(result, ensure_ascii=False))

    def connect(self, conversation_id=None, origin="http://localhost:5173"):
        return self.client.websocket_connect(f"/api/agent/ws?conversation_id={conversation_id or self.conversation_id}",
                                             headers={"origin": origin})

    @staticmethod
    def until(ws, kind):
        messages = []
        for _ in range(30):
            item = ws.receive_json()
            messages.append(item)
            if item["type"] == kind:
                return messages
        raise AssertionError(f"Did not receive {kind}: {messages}")

    def seed_order(self):
        with self.factory() as db:
            customer = CustomerModel(name="测试客户", phone="13800138000")
            db.add(customer)
            db.flush()
            address = CustomerAddressModel(customer_id=customer.id, recipient_name="测试客户", phone="13800138000",
                                           country="CN", province="省", city="市", district="区", detail="测试地址")
            product = ProductModel(name="测试商品", category="Test", price=Decimal("10.00"), stock=10,
                                   status="Active", image_url="/test.png", color="red", rating=4, reviews=0, shipping="Standard")
            db.add_all([address, product])
            db.commit()
            order, error = OrderCRUD(db).create_order(OrderCreate(customer_id=customer.id, address_id=address.id,
                                                                  items=[{"product_id": product.id, "quantity": 2}]))
            self.assertIsNone(error)
            return order.id, product.id

    def prepare(self, order_id):
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        self.store.begin_turn(self.conversation_id, 1, request_id, "取消订单，原因：买错了")
        result = self.store.execute_tool(self.conversation_id, 1, request_id, "prepare_cancel_order",
                                         {"order_id": order_id, "reason": "买错了"})
        self.store.release(self.conversation_id, 1, request_id)
        return result["action"]

    def confirm(self, action_id, token=None):
        request_id = uuid4().hex
        actor = self.store.identity(token or self.token)
        self.store.claim(self.conversation_id, actor["id"], request_id)
        try:
            return self.store.confirm(self.conversation_id, token or self.token, action_id, request_id)
        finally:
            self.store.release(self.conversation_id, actor["id"], request_id)

    def test_http_auth_and_status_do_not_expose_key(self):
        response = self.client.get("/api/agent/status")
        self.assertTrue(response.json()["capabilities"]["chat"])
        self.assertNotIn("test-only-key", response.text)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/agent/status").status_code, 401)
        self.assertEqual(self.client.post("/api/agent/conversations").status_code, 401)

    def test_archival_is_idempotent_and_conversation_cannot_be_reopened(self):
        path = f"/api/agent/conversations/{self.conversation_id}"
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get(path).status_code, 404)
        with self.assertRaises(WebSocketDisconnect):
            with self.connect():
                pass
        with self.factory() as db:
            self.assertIsNotNone(db.get(AgentConversation, self.conversation_id))
            self.assertIsNotNone(db.get(AgentConversationArchive, self.conversation_id))

    def test_archival_requires_login_and_conversation_ownership(self):
        path = f"/api/agent/conversations/{self.conversation_id}"
        self.client.cookies.clear()
        self.assertEqual(self.client.delete(path).status_code, 401)
        self.client.cookies.set(COOKIE_NAME, self.other_token)
        self.assertEqual(self.client.delete(path).status_code, 404)
        with self.factory() as db:
            self.assertIsNone(db.get(AgentConversationArchive, self.conversation_id))

    def test_busy_conversation_must_finish_before_archival(self):
        path = f"/api/agent/conversations/{self.conversation_id}"
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        self.assertEqual(self.client.delete(path).status_code, 409)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.store.release(self.conversation_id, 1, request_id)
        self.assertEqual(self.client.delete(path).status_code, 204)

    def test_archival_invalidates_pending_action_and_preserves_orders_and_tickets(self):
        order_id, product_id = self.seed_order()
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        self.store.begin_turn(self.conversation_id, 1, request_id, "请人工协助")
        ticket = self.store.execute_tool(self.conversation_id, 1, request_id, "create_handoff_ticket", {"summary": "测试工单"})["ticket"]
        self.store.release(self.conversation_id, 1, request_id)
        action = self.prepare(order_id)
        self.assertEqual(self.client.delete(f"/api/agent/conversations/{self.conversation_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/agent/tickets/{ticket['id']}").status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.get(AgentAction, action["id"]).state, "cancelled")
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.PENDING_PAYMENT)
            self.assertEqual(db.get(ProductModel, product_id).stock, 8)
            self.assertEqual(db.get(AgentTicket, ticket["id"]).state, "pending")
        with self.assertRaises(AgentError):
            self.store.claim(self.conversation_id, 1, uuid4().hex)

    def test_preexisting_websocket_cannot_start_new_work_after_archival(self):
        with self.connect() as ws:
            ws.receive_json()
            self.assertEqual(self.client.delete(f"/api/agent/conversations/{self.conversation_id}").status_code, 204)
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex, "text": "查询订单", "speak": False})
            self.assertEqual(ws.receive_json()["code"], "not_found")
        self.assertEqual(len(self.provider.calls), 0)

    def test_websocket_rejects_missing_login_foreign_origin_and_other_users_conversation(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.connect(origin="https://untrusted.example"):
                pass
        self.client.cookies.set(COOKIE_NAME, self.other_token)
        with self.assertRaises(WebSocketDisconnect):
            with self.connect():
                pass
        self.assertEqual(self.client.get(f"/api/agent/conversations/{self.conversation_id}").status_code, 404)
        self.client.cookies.clear()
        with self.assertRaises(WebSocketDisconnect):
            with self.connect():
                pass

    def test_chat_answer_persists_and_duplicate_request_replays_without_model_call(self):
        event = {"type": "chat.send", "request_id": uuid4().hex, "text": "如何查订单", "speak": False}
        with self.connect() as ws:
            self.assertEqual(ws.receive_json()["type"], "session.ready")
            ws.send_json(event)
            received = self.until(ws, "turn.done")
            self.assertEqual(received[-1]["status"], "completed")
            ws.send_json(event)
            replay = self.until(ws, "turn.done")
            self.assertTrue(replay[0]["replay"])
        self.assertEqual(len(self.provider.calls), 1)
        history = self.client.get(f"/api/agent/conversations/{self.conversation_id}").json()
        self.assertEqual(len(history["messages"]), 2)
        self.assertFalse(history["busy"])

    def test_voice_is_not_submitted_until_release_and_cancel_does_not_call_model(self):
        with self.connect() as ws:
            ws.receive_json()
            request_id = uuid4().hex
            ws.send_json({"type": "audio.start", "request_id": request_id, "speak": False})
            self.until(ws, "audio.ready")
            ws.send_bytes(b"\x01\x00" * 6000)
            self.until(ws, "transcript")
            ws.send_json({"type": "ping"})
            self.until(ws, "pong")
            self.assertEqual(len(self.provider.calls), 0)
            ws.send_json({"type": "turn.cancel", "request_id": request_id})
            self.assertEqual(self.until(ws, "turn.done")[-1]["status"], "cancelled")
            self.assertEqual(len(self.provider.calls), 0)
            request_id = uuid4().hex
            ws.send_json({"type": "audio.start", "request_id": request_id, "speak": False})
            self.until(ws, "audio.ready")
            ws.send_bytes(b"\x01\x00" * 6000)
            ws.send_json({"type": "audio.commit", "request_id": request_id})
            received = self.until(ws, "turn.done")
            self.assertEqual(received[-1]["status"], "completed")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(self.provider.recordings[0]), 12000)
        history = self.store.conversation(self.conversation_id, 1)["messages"]
        self.assertEqual(history[0]["text"], "查询订单")

    def test_short_audio_and_invalid_format_never_call_model(self):
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "audio.start", "request_id": uuid4().hex, "sample_rate": 48000})
            self.assertEqual(ws.receive_json()["code"], "invalid_event")
            request_id = uuid4().hex
            ws.send_json({"type": "audio.start", "request_id": request_id})
            self.until(ws, "audio.ready")
            ws.send_bytes(b"\x00\x00" * 10)
            ws.send_json({"type": "audio.commit", "request_id": request_id})
            self.until(ws, "turn.done")
            self.assertEqual(ws.receive_json()["code"], "audio_too_short")
        self.assertEqual(len(self.provider.calls), 0)

    def test_confirm_preview_does_not_write_and_confirm_restores_stock_only_once(self):
        order_id, product_id = self.seed_order()
        action = self.prepare(order_id)
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.PENDING_PAYMENT)
            self.assertEqual(db.get(ProductModel, product_id).stock, 8)
        first = self.confirm(action["id"])
        second = self.confirm(action["id"])
        self.assertEqual(first, second)
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.CANCELLED)
            self.assertEqual(db.get(ProductModel, product_id).stock, 10)
            self.assertEqual(db.scalar(select(func.count()).select_from(OrderEvent).where(
                OrderEvent.idempotency_key == f"status:agent:{action['id']}")), 1)
            self.assertEqual(db.get(AgentAction, action["id"]).state, "completed")

    def test_changed_order_requires_new_confirmation(self):
        order_id, _ = self.seed_order()
        action = self.prepare(order_id)
        with self.factory() as db:
            OrderCRUD(db).update_status(order_id, OrderStatus.PAID_PENDING_SHIPMENT)
        with self.assertRaisesRegex(AgentError, "信息已变化"):
            self.confirm(action["id"])
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.PAID_PENDING_SHIPMENT)
            self.assertEqual(db.get(AgentAction, action["id"]).state, "stale")

    def test_expired_superseded_and_other_account_cannot_confirm(self):
        order_id, _ = self.seed_order()
        action = self.prepare(order_id)
        with self.assertRaises(AgentError):
            self.confirm(action["id"], self.other_token)
        with self.factory() as db:
            db.get(AgentAction, action["id"]).expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        with self.assertRaisesRegex(AgentError, "过期"):
            self.confirm(action["id"])
        action = self.prepare(order_id)
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        self.store.begin_turn(self.conversation_id, 1, request_id, "先查另一个订单")
        self.store.release(self.conversation_id, 1, request_id)
        with self.assertRaisesRegex(AgentError, "失效"):
            self.confirm(action["id"])

    def test_business_failure_rolls_back_action_and_order_together(self):
        order_id, product_id = self.seed_order()
        action = self.prepare(order_id)
        with patch("app.features.orders.crud.add_event", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                self.confirm(action["id"])
        with self.factory() as db:
            self.assertEqual(db.get(AgentAction, action["id"]).state, "pending")
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.PENDING_PAYMENT)
            self.assertEqual(db.get(ProductModel, product_id).stock, 8)

    def test_typed_and_voice_confirmation_use_server_action(self):
        order_id, _ = self.seed_order()
        action = self.prepare(order_id)
        self.provider.recognized_text = "确认"
        with self.connect() as ws:
            ws.receive_json()
            request_id = uuid4().hex
            ws.send_json({"type": "audio.start", "request_id": request_id, "speak": False})
            self.until(ws, "audio.ready")
            ws.send_bytes(b"\x01\x00" * 6000)
            ws.send_json({"type": "audio.commit", "request_id": request_id})
            result = self.until(ws, "turn.done")
            self.assertTrue(any(m["type"] == "action.result" for m in result))
            ws.send_json({"type": "action.confirm", "request_id": uuid4().hex, "action_id": action["id"], "speak": False})
            result = self.until(ws, "turn.done")
            self.assertEqual(result[-1]["status"], "completed")
        self.assertEqual(len(self.provider.calls), 0)
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.CANCELLED)

    def test_model_prepares_action_and_receives_real_order_data(self):
        order_id, _ = self.seed_order()
        self.provider.responses = [
            {"tool_calls": [{"id": "query1", "type": "function", "function": {
                "name": "get_order", "arguments": json.dumps({"order_id": order_id})}}]},
            {"tool_calls": [{"id": "prepare1", "type": "function", "function": {
                "name": "prepare_cancel_order", "arguments": json.dumps({"order_id": order_id, "reason": "买错了"})}}]},
            {"content": '{"text":"订单已取消（错误模型输出）","speech_text":"已取消"}'},
        ]
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex,
                          "text": f"取消订单{order_id}，原因买错了", "speak": False})
            messages = self.until(ws, "turn.done")
            required = next(m for m in messages if m["type"] == "action.required")
            answer = next(m["message"] for m in messages if m["type"] == "assistant.message")
            self.assertIn("当前尚未执行", answer["text"])
            self.assertEqual(required["action"]["order_id"], order_id)
        tool_data = json.loads(self.provider.calls[1][-1]["content"])
        self.assertEqual(tool_data["total"], "20.00")
        self.assertEqual(tool_data["recipient_phone"], "138****8000")
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.PENDING_PAYMENT)

    def test_speech_error_keeps_successful_text_answer(self):
        self.provider.speech_fails = True
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex, "text": "你好"})
            messages = self.until(ws, "turn.done")
            self.assertTrue(any(m["type"] == "assistant.message" for m in messages))
            self.assertTrue(any(m["type"] == "warning" for m in messages))
            self.assertEqual(messages[-1]["status"], "completed")

    def test_cancel_preview_claim_requires_real_tool_action(self):
        order_id, _ = self.seed_order()
        self.provider.responses = [
            {"content": '{"text":"已生成待确认的取消操作，请确认。","speech_text":"请确认"}'},
            {"tool_calls": [{"id": "actual-action", "type": "function", "function": {
                "name": "prepare_cancel_order", "arguments": json.dumps({"order_id": order_id, "reason": "买错了"})}}]},
        ]
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex,
                          "text": f"取消订单{order_id}，原因买错了", "speak": False})
            messages = self.until(ws, "turn.done")
            self.assertTrue(any(message["type"] == "action.required" for message in messages))
            self.assertEqual(len(self.provider.calls), 2)
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.PENDING_PAYMENT)
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentAction)), 1)

    def test_repeated_fake_cancel_previews_are_not_delivered(self):
        self.provider.responses = [{"content": '{"text":"已生成待确认的取消操作。"}'}] * 4
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex,
                          "text": "取消订单ORD-TEST，原因买错了", "speak": False})
            messages = self.until(ws, "turn.done")
            self.assertFalse(any(message["type"] == "assistant.message" for message in messages))
            self.assertEqual(messages[-1]["status"], "failed")
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentAction)), 0)

    def test_pcm_output_has_format_header_and_end(self):
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex, "text": "你好"})
            self.until(ws, "audio.start")
            self.assertEqual(ws.receive_bytes(), b"\x00\x00" * 20)
            self.assertTrue(ws.receive_json()["completed"])
            self.assertEqual(ws.receive_json()["type"], "turn.done")

    def test_voice_confirmation_interrupts_question_audio_without_losing_pending_action(self):
        order_id, _ = self.seed_order()
        self.provider.block_speech = True
        self.provider.recognized_text = "确认"
        self.provider.responses = [{"tool_calls": [{"id": "prepare1", "type": "function", "function": {
            "name": "prepare_cancel_order", "arguments": json.dumps({"order_id": order_id, "reason": "买错了"})}}]}]
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex, "text": "取消订单，原因买错了"})
            self.until(ws, "audio.start")
            ws.receive_bytes()
            request_id = uuid4().hex
            ws.send_json({"type": "audio.start", "request_id": request_id, "speak": False})
            messages = self.until(ws, "audio.ready")
            self.assertTrue(any(m["type"] == "turn.done" and m["status"] == "cancelled" for m in messages))
            ws.send_bytes(b"\x01\x00" * 6000)
            ws.send_json({"type": "audio.commit", "request_id": request_id})
            messages = self.until(ws, "turn.done")
            self.assertTrue(any(m["type"] == "action.result" for m in messages))
        with self.factory() as db:
            self.assertEqual(db.get(OrderModel, order_id).status, OrderStatus.CANCELLED)

    def test_unconfigured_service_is_honest_and_handoff_remains_available(self):
        self.provider.settings = replace(self.settings, api_key="")
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "chat.send", "request_id": uuid4().hex, "text": "你好"})
            self.assertEqual(ws.receive_json()["code"], "service_unconfigured")
            ws.send_json({"type": "handoff.request", "request_id": uuid4().hex, "summary": "请核实退货政策"})
            messages = self.until(ws, "turn.done")
            ticket = next(m["ticket"] for m in messages if m["type"] == "ticket.created")
        detail = self.client.get(f"/api/agent/tickets/{ticket['id']}").json()
        self.assertEqual(detail["state"], "pending")
        self.assertIn("退货政策", detail["transcript"][0]["text"])
        self.assertEqual(self.client.patch(f"/api/agent/tickets/{ticket['id']}", json={"state": "resolved"}).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/agent/tickets/{ticket['id']}", json={"state": "in_progress"}).status_code, 409)

    def test_logout_during_websocket_rejects_next_command(self):
        with self.connect() as ws:
            ws.receive_json()
            with self.factory() as db:
                session = db.scalar(select(AuthSessionModel).where(AuthSessionModel.user_id == 1))
                db.delete(session)
                db.commit()
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json()["code"], "unauthorized")
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()

    def test_disconnect_cancels_recording_and_releases_conversation_lease(self):
        with self.connect() as ws:
            ws.receive_json()
            ws.send_json({"type": "audio.start", "request_id": uuid4().hex})
            self.until(ws, "audio.ready")
        self.assertFalse(self.store.conversation(self.conversation_id, 1)["busy"])
        self.assertEqual(len(self.provider.calls), 0)

    def test_cancel_before_worker_starts_still_releases_lease(self):
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        sent = []

        class Socket:
            async def send_json(self, item):
                sent.append(item)

        async def exercise():
            handler = agent_routes.AgentSocket(Socket(), self.conversation_id, self.token,
                                                {"id": 1}, self.provider)
            handler.request_id = request_id
            handler.task = asyncio.create_task(asyncio.sleep(100))
            await handler.cancel_active()

        asyncio.run(exercise())
        self.assertFalse(self.store.conversation(self.conversation_id, 1)["busy"])
        self.assertEqual(sent[-1]["status"], "cancelled")

    def test_other_connection_cannot_start_a_turn_while_conversation_is_busy(self):
        request_id = uuid4().hex
        self.store.claim(self.conversation_id, 1, request_id)
        try:
            with self.connect() as ws:
                ws.receive_json()
                ws.send_json({"type": "chat.send", "request_id": uuid4().hex, "text": "查询订单"})
                self.assertEqual(ws.receive_json()["code"], "busy")
            self.assertEqual(len(self.provider.calls), 0)
        finally:
            self.store.release(self.conversation_id, 1, request_id)


class SpeechSocket:
    """本地实现百炼任务握手，用于验证发送顺序和二进制帧；不访问网络。"""
    def __init__(self, mode):
        self.mode = mode
        self.events = asyncio.Queue()
        self.commands = []
        self.audio = bytearray()
        self.received_audio = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, raw):
        if isinstance(raw, bytes):
            self.audio.extend(raw)
            self.received_audio.set()
            return
        command = json.loads(raw)
        self.commands.append(command)
        action = command["header"]["action"]
        if action == "run-task":
            await self.events.put(json.dumps({"header": {"event": "task-started"}}))
        if action == "continue-task" and self.mode == "tts":
            await self.events.put(b"\x01\x00" * 50)
        if action == "finish-task":
            if self.mode == "asr":
                for begin, text in [(0, "查询"), (0, "查询"), (500, "订单")]:
                    await self.events.put(json.dumps({"header": {"event": "result-generated"}, "payload": {
                        "output": {"sentence": {"begin_time": begin, "text": text, "sentence_end": True}}}}))
            await self.events.put(json.dumps({"header": {"event": "task-finished"}}))

    async def recv(self):
        return await self.events.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_asr_adapter_waits_for_commit_and_deduplicates_final_sentences(self):
        socket = SpeechSocket("asr")
        settings = replace(AgentSettings.from_env(), api_key="test-only-key")
        provider = BailianProvider(settings)
        queue = asyncio.Queue()
        await queue.put(b"\x00\x00" * 6000)
        transcripts = []

        async def on_text(text, final):
            transcripts.append((text, final))

        with patch.object(provider, "_connect", return_value=socket):
            task = asyncio.create_task(provider.recognize(queue, on_text))
            await asyncio.wait_for(socket.received_audio.wait(), 2)
            self.assertFalse(task.done())
            self.assertEqual([c["header"]["action"] for c in socket.commands], ["run-task"])
            await queue.put(None)
            self.assertEqual(await asyncio.wait_for(task, 2), "查询订单")
        self.assertEqual(transcripts[-1], ("查询订单", True))
        self.assertEqual(len(socket.audio), 12000)
        self.assertEqual(socket.commands[0]["header"]["task_id"], socket.commands[-1]["header"]["task_id"])

    async def test_real_tts_adapter_sends_summary_and_returns_pcm_frames(self):
        socket = SpeechSocket("tts")
        provider = BailianProvider(replace(AgentSettings.from_env(), api_key="test-only-key"))
        with patch.object(provider, "_connect", return_value=socket):
            chunks = [chunk async for chunk in provider.synthesize("订单正在配送中。")]
        self.assertEqual(chunks, [b"\x01\x00" * 50])
        self.assertEqual([c["header"]["action"] for c in socket.commands], ["run-task", "continue-task", "finish-task"])
        self.assertEqual(socket.commands[0]["payload"]["parameters"]["format"], "pcm")
        self.assertEqual(socket.commands[0]["payload"]["parameters"]["sample_rate"], 24000)
        self.assertEqual(socket.commands[1]["payload"]["input"]["text"], "订单正在配送中。")

    async def test_http_auth_failure_and_rate_limit_do_not_expose_provider_body(self):
        provider = BailianProvider(replace(AgentSettings.from_env(), api_key="private-key"))
        original_client = httpx.AsyncClient
        for status, code in [(401, "provider_auth"), (429, "provider_busy")]:
            transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"message": "private-key"}))
            with patch("app.features.agent.provider.httpx.AsyncClient", return_value=original_client(transport=transport)):
                with self.assertRaises(AgentError) as error:
                    await provider.complete([], [])
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("private-key", str(error.exception))

    async def test_missing_knowledge_base_does_not_make_network_request(self):
        settings = replace(AgentSettings.from_env(), api_key="", knowledge_base_id="", rag_url="")
        with patch("app.features.agent.provider.httpx.AsyncClient") as client:
            result = await BailianProvider(settings).retrieve("退货规则")
        self.assertFalse(result["configured"])
        self.assertEqual(result["documents"], [])
        client.assert_not_called()

    async def test_provider_error_does_not_echo_raw_credentials(self):
        with self.assertRaises(AgentError) as error:
            BailianProvider._event(json.dumps({"header": {"event": "task-failed", "error_message": "SECRET_KEY"}}))
        self.assertNotIn("SECRET_KEY", str(error.exception))


if __name__ == "__main__":
    unittest.main()
