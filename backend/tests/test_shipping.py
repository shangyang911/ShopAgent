"""Proxy outages must be actionable and distinct from invalid delivery addresses."""
import unittest
import warnings
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.shipping.router import router
from app.features.shipping.delivery.service import AmapServiceError, AmapServiceUnavailable, ShippingService
from app.features.orders.models import OrderStatus


def delivery_order(**overrides):
    return SimpleNamespace(**{
        "id": 14, "order_number": "ORD-SNAPSHOT", "status": OrderStatus.IN_DELIVERY,
        "total": Decimal("88.20"), "recipient_name": "下单时收件人", "recipient_phone": "13800000000",
        "province": "浙江省", "city": "杭州市", "district": "西湖区", "address_detail": "下单时门牌号",
        "longitude": 120.1, "latitude": 30.2, **overrides,
    })


class DeliveryQueueTests(unittest.IsolatedAsyncioTestCase):
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
