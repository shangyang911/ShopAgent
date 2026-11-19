"""Proxy outages must be actionable and distinct from invalid delivery addresses."""
import asyncio
import unittest
import warnings
from datetime import datetime
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.customers.models import CustomerModel
from app.features.shipping.router import router
from app.features.shipping.delivery.service import AmapServiceError, AmapServiceUnavailable, ShippingService
from app.features.orders.models import OrderModel, OrderStatus


def delivery_order(**overrides):
    return SimpleNamespace(**{
        "id": 14, "order_number": "ORD-SNAPSHOT", "status": OrderStatus.IN_DELIVERY,
        "total": Decimal("88.20"), "recipient_name": "下单时收件人", "recipient_phone": "13800000000",
        "province": "浙江省", "city": "杭州市", "district": "西湖区", "address_detail": "下单时门牌号",
        "longitude": 120.1, "latitude": 30.2, **overrides,
    })


class DeliveryQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_geocoding_has_one_budget_and_preserves_cached_deliveries(self):
        missing = delivery_order(longitude=None, latitude=None)
        another_missing = delivery_order(id=15, longitude=None, latitude=None)
        located = delivery_order(id=16)
        service = ShippingService(SimpleNamespace(commit=lambda: None))

        async def slow_amap(*_):
            await asyncio.sleep(0.15)
            return {"status": "1", "geocodes": [{"location": "120.1,30.2"}]}

        with (
            patch.object(service, "list_delivery_orders", return_value=[missing, another_missing, located]),
            patch.object(service, "_request_amap", new=slow_amap),
            patch("app.features.shipping.delivery.service.DELIVERY_LIST_GEOCODE_BUDGET_SECONDS", 0.05, create=True),
        ):
            started = asyncio.get_running_loop().time()
            rows = await asyncio.wait_for(service.list_delivery_orders_with_coordinates(), timeout=0.5)
            elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 0.15, "The list must not wait for each order's upstream timeout")
        self.assertEqual([row.id for row in rows], [14, 15, 16])
        for row in rows[:2]:
            self.assertIsNone(row.longitude)
            self.assertIn("超时", row.geocode_error)
        self.assertEqual(rows[2].longitude, 120.1)
        self.assertIsNone(rows[2].geocode_error)

    async def test_budget_is_shared_across_successful_and_pending_geocoding(self):
        first = delivery_order(longitude=None, latitude=None)
        second = delivery_order(id=15, longitude=None, latitude=None)
        service = ShippingService(SimpleNamespace(commit=lambda: None))

        async def delayed_amap(*_):
            await asyncio.sleep(0.04)
            return {"status": "1", "geocodes": [{"location": "120.1,30.2"}]}

        with (
            patch.object(service, "list_delivery_orders", return_value=[first, second]),
            patch.object(service, "_request_amap", new=delayed_amap),
            patch("app.features.shipping.delivery.service.DELIVERY_LIST_GEOCODE_BUDGET_SECONDS", 0.06, create=True),
        ):
            rows = await service.list_delivery_orders_with_coordinates()

        self.assertEqual(rows[0].longitude, 120.1)
        self.assertIsNone(rows[0].geocode_error)
        self.assertIsNone(rows[1].longitude)
        self.assertIn("超时", rows[1].geocode_error)

    async def test_geocode_failure_preserves_order_snapshot_and_other_deliveries(self):
        missing = delivery_order(longitude=None, latitude=None)
        located = delivery_order(id=15, order_number="ORD-LOCATED")
        service = ShippingService(None)
        with (
            patch.object(service, "list_delivery_orders", return_value=[missing, located]),
            patch.object(service, "resolve_order_coordinates", new=AsyncMock(side_effect=AmapServiceError("无法解析门牌号"))),
        ):
            rows = await service.list_delivery_orders_with_coordinates()
        self.assertEqual([row.id for row in rows], [14, 15])
        self.assertEqual(rows[0].geocode_error, "无法解析门牌号")
        self.assertIsNone(rows[0].longitude)
        self.assertEqual(rows[0].recipient_name, "下单时收件人")
        self.assertEqual(rows[0].address_detail, "下单时门牌号")
        self.assertEqual(rows[0].model_dump(mode="json")["total"], "88.20")
        self.assertEqual(rows[1].longitude, 120.1)
        self.assertIsNone(rows[1].geocode_error)


class DeliveryPaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.db.close)
        self.db.add(CustomerModel(id=1, name="Delivery test", phone="10000"))
        self.db.add_all([
            OrderModel(
                **vars(delivery_order(id=index, order_number=f"ORD-{index}",
                                      status=OrderStatus.COMPLETED if index == 206 else OrderStatus.IN_DELIVERY)),
                customer_id=1, subtotal=Decimal("88.20"), country="CN", created_at=datetime(2026, 1, 1),
            )
            for index in range(1, 207)
        ])
        self.db.commit()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[require_current_user] = lambda: object()
        self.app.dependency_overrides[get_db] = lambda: self.db

    async def test_legacy_list_defaults_to_one_hundred_orders_and_keeps_array_shape(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            response = await client.get("/api/shipping/orders")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsInstance(response.json(), list)
        self.assertEqual([row["id"] for row in response.json()], list(range(1, 101)))

    async def test_legacy_list_paginates_stably_and_excludes_inactive_orders(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            second = await client.get("/api/shipping/orders?page=2&page_size=100")
            last = await client.get("/api/shipping/orders?page=3&page_size=100")
            empty = await client.get("/api/shipping/orders?page=4&page_size=100")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual([row["id"] for row in second.json()], list(range(101, 201)))
        self.assertEqual([row["id"] for row in last.json()], [201, 202, 203, 204, 205])
        self.assertEqual(empty.json(), [])

    async def test_legacy_list_accepts_two_hundred_and_rejects_unbounded_or_invalid_pages(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            maximum = await client.get("/api/shipping/orders?page_size=200")
            self.assertEqual(maximum.status_code, 200, maximum.text)
            self.assertEqual(len(maximum.json()), 200)
            for query in ("page_size=201", "page_size=0", "page=0", "page=-1"):
                with self.subTest(query=query):
                    response = await client.get(f"/api/shipping/orders?{query}")
                    self.assertEqual(response.status_code, 422, response.text)

    async def test_single_route_still_geocodes_and_persists_when_list_budget_is_exhausted(self):
        order = self.db.get(OrderModel, 1)
        order.longitude = order.latitude = None
        self.db.commit()
        with (
            patch("app.features.shipping.delivery.service.DELIVERY_LIST_GEOCODE_BUDGET_SECONDS", 0, create=True),
            patch("app.features.shipping.delivery.service.AMAP_SHIPPING_ORIGIN_LONGITUDE", "120.0"),
            patch("app.features.shipping.delivery.service.AMAP_SHIPPING_ORIGIN_LATITUDE", "30.0"),
            patch.object(ShippingService, "_request_amap", new=AsyncMock(side_effect=[
                {"status": "1", "geocodes": [{"location": "120.1,30.2"}]},
                {"status": "1", "route": {"paths": [{"distance": "1200", "duration": "120", "steps": []}]}},
            ])),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                response = await client.get("/api/shipping/orders/1/route")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["destination"], [120.1, 30.2])
        self.assertEqual(response.json()["distance_meters"], 1200)
        self.assertEqual(response.json()["polyline"], [[120.0, 30.0], [120.1, 30.2]])
        self.db.expire_all()
        saved = self.db.get(OrderModel, 1)
        self.assertEqual((saved.longitude, saved.latitude), (120.1, 30.2))


class AmapRequestTests(unittest.IsolatedAsyncioTestCase):
    async def request_with_transport(self, handler, proxy="http://127.0.0.1:8080/_AMapService"):
        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with (
            patch("app.features.shipping.delivery.service.AMAP_WEB_KEY", "test-key"),
            patch("app.features.shipping.delivery.service.AMAP_SERVICE_PROXY_URL", proxy),
            patch("app.features.shipping.delivery.service.httpx.AsyncClient", side_effect=lambda **kwargs: client_type(transport=transport, **kwargs)),
        ):
            return await ShippingService(None)._request_amap("/v3/direction/driving", {})

    async def test_missing_local_proxy_includes_startup_command(self):
        def refused(request):
            raise httpx.ConnectError("connection refused", request=request)

        with self.assertRaises(AmapServiceUnavailable) as caught:
            await self.request_with_transport(refused)
        self.assertIn("dev_amap_proxy.py", str(caught.exception))
        self.assertIn("AMAP_SERVICE_PROXY_URL", str(caught.exception))
        self.assertNotIn("test-key", str(caught.exception))

    async def test_remote_proxy_failure_has_no_local_startup_hint(self):
        def refused(request):
            raise httpx.ConnectError("connection refused", request=request)

        with self.assertRaises(AmapServiceUnavailable) as caught:
            await self.request_with_transport(refused, "https://proxy.example/_AMapService")
        self.assertNotIn("dev_amap_proxy.py", str(caught.exception))

    async def test_timeout_is_service_unavailability(self):
        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)

        with self.assertRaises(AmapServiceUnavailable):
            await self.request_with_transport(timeout)

    async def test_upstream_failure_is_service_unavailability(self):
        with self.assertRaises(AmapServiceUnavailable):
            await self.request_with_transport(lambda _: httpx.Response(502, text="Bad Gateway"))

    async def test_invalid_json_is_service_unavailability(self):
        with self.assertRaises(AmapServiceUnavailable):
            await self.request_with_transport(lambda _: httpx.Response(200, text="not JSON"))

    async def test_successful_response_is_preserved(self):
        payload = {"status": "1", "route": {"paths": [{"distance": "1000"}]}}
        result = await self.request_with_transport(lambda _: httpx.Response(200, json=payload))
        self.assertEqual(result, payload)


class ShippingRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[require_current_user] = lambda: object()
        self.app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda *_: object())

    async def get_route_with_error(self, error):
        with patch.object(ShippingService, "get_delivery_route", new=AsyncMock(side_effect=error)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", DeprecationWarning)
                    return await client.get("/api/shipping/orders/14/route")

    async def test_unavailable_proxy_returns_503_with_detail(self):
        response = await self.get_route_with_error(AmapServiceUnavailable("Start the local proxy"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Start the local proxy")

    async def test_invalid_address_remains_422_without_deprecation_warning(self):
        response = await self.get_route_with_error(AmapServiceError("Invalid address"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Invalid address")

    async def test_invalid_delivery_status_has_no_deprecation_warning(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                response = await client.patch("/api/shipping/orders/14/status", json={"status": "cancelled"})
        self.assertEqual(response.status_code, 422)

    async def test_successful_status_update_returns_the_order_address_snapshot(self):
        order = delivery_order(status=OrderStatus.COMPLETED)
        with patch("app.features.shipping.delivery.router.OrderCRUD.update_status", return_value=(order, None)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                response = await client.patch("/api/shipping/orders/14/status", json={"status": "completed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["total"], "88.20")
        self.assertEqual(response.json()["address_detail"], "下单时门牌号")
        self.assertIsNone(response.json()["geocode_error"])


if __name__ == "__main__":
    unittest.main()
