"""Isolated business acceptance for deterministic recommendations and confirmed draft creation."""
import unittest
from datetime import timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from evals.fixtures import Fixture
from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.auth.models import UserModel
from app.features.inventory.copilot import replenishment, shipping_analysis
from app.features.inventory.copilot_router import router
from app.features.operations.models import Inbound, OrderFacts, utcnow
from app.features.products.models import ProductSKU
from app.features.agent.service import AgentEngine
from evals.runner import ReplayProvider


class CopilotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        app = FastAPI()
        app.include_router(router, prefix='/api')
        def database():
            with self.fixture.factory() as db:
                yield db
        def actor():
            with self.fixture.factory() as db:
                return db.get(UserModel, 1)
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[require_current_user] = actor
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.fixture.close()

    def plan(self):
        response = self.client.get('/api/copilot/replenishment?minimum_stock=20')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def body(self, report):
        row = report['items'][0]
        return {'parameters': report['parameters'], 'items': [{k: row[k] for k in ('product_id', 'sku_id', 'basis')} | {'quantity': row['suggested_quantity']}],
                'idempotency_key': 'copilot-intent-001'}

    def test_paid_demand_excludes_unpaid_and_does_not_subtract_reserved_twice(self):
        report = self.plan()
        row = report['items'][0]
        self.assertEqual(row['paid_units_7d'], 2)
        self.assertEqual(row['paid_units_30d'], 2)
        self.assertEqual(row['available'], 6)
        self.assertEqual(row['reserved'], 4)
        self.assertEqual(row['suggested_quantity'], 14)

    def test_draft_requires_confirmation_intent_and_never_moves_inventory(self):
        before = self.fixture.snapshot()
        report = self.plan()
        self.assertEqual(self.fixture.snapshot(), before)
        with self.fixture.factory() as db:
            self.assertIsNone(db.scalar(select(Inbound)))
        body = self.body(report)
        result = self.client.post('/api/copilot/replenishment/draft', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'draft')
        self.assertIn('核对来源', result.json()['remark'])
        self.assertEqual(self.fixture.snapshot(), before)
        retry = self.client.post('/api/copilot/replenishment/draft', json=body)
        self.assertEqual(retry.json()['id'], result.json()['id'])
        current = self.plan()['items'][0]
        self.assertEqual(current['draft_quantity'], 14)
        self.assertEqual(current['suggested_quantity'], 0)

    def test_stale_recommendation_and_tampered_quantity_are_rejected(self):
        body = self.body(self.plan())
        body['items'][0]['quantity'] += 1
        self.assertEqual(self.client.post('/api/copilot/replenishment/draft', json=body).status_code, 409)
        body = self.body(self.plan())
        with self.fixture.factory() as db:
            sku = db.scalar(select(ProductSKU))
            sku.revision += 1
            db.commit()
        self.assertEqual(self.client.post('/api/copilot/replenishment/draft', json=body).status_code, 409)

    def test_idempotency_conflict_and_repeated_sku_are_rejected(self):
        body = self.body(self.plan())
        self.assertEqual(self.client.post('/api/copilot/replenishment/draft', json=body).status_code, 200)
        body['parameters']['minimum_stock'] = 21
        self.assertEqual(self.client.post('/api/copilot/replenishment/draft', json=body).status_code, 409)
        body['items'].append(body['items'][0])
        self.assertEqual(self.client.post('/api/copilot/replenishment/draft', json=body).status_code, 422)

    def test_no_history_uses_floor_and_does_not_invent_coverage(self):
        with self.fixture.factory() as db:
            facts = db.get(OrderFacts, 2)
            facts.paid_at = utcnow()-timedelta(days=60)
            db.commit()
            report = replenishment(db, minimum_stock=20)
        row = report['items'][0]
        self.assertEqual(row['paid_units_30d'], 0)
        self.assertIsNone(row['coverage_days'])
        self.assertIn('缺少', row['confidence'])

    def test_shipping_analysis_uses_actual_missing_waybill_and_payment_time(self):
        with self.fixture.factory() as db:
            facts = db.get(OrderFacts, 2)
            facts.paid_at = utcnow()-timedelta(hours=25)
            db.commit()
            report = shipping_analysis(db)
        self.assertEqual(len(report['items']), 1)
        self.assertTrue(report['items'][0]['overdue'])
        self.assertIn('尚未登记主运单', report['items'][0]['reasons'])

    def test_bounds_and_authentication(self):
        self.assertEqual(self.client.get('/api/copilot/replenishment?coverage_days=0').status_code, 422)
        app = FastAPI()
        app.include_router(router, prefix='/api')
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/copilot/replenishment').status_code, 401)


if __name__ == '__main__':
    unittest.main()
