import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
import importlib
from evals.fixtures import Fixture
from test_agent import FakeProvider
from app.db.session import get_db
from app.features.agent.settings import AgentSettings
from app.features.agent.observability import router
from app.features.agent.telemetry import AgentTrace, Trace, usage_and_cost, redacted
from app.features.agent.product_tools import search_products
from app.features.agent.provider import AgentError
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user, COOKIE_NAME
from app.features.products.models import ProductModel, ProductSKU

agent_routes = importlib.import_module('app.features.agent.router')


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.provider = FakeProvider(replace(AgentSettings.from_env(), api_key='synthetic-key'))
        app = FastAPI()
        app.include_router(agent_routes.router, prefix='/api')
        app.include_router(router, prefix='/api')
        def database():
            with self.fixture.factory() as db:
                yield db
        def actor():
            with self.fixture.factory() as db:
                return db.get(UserModel, 1)
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[require_current_user] = actor
        self.app = app
        self.patches = [patch.object(agent_routes, 'store', self.fixture.store),
            patch.object(agent_routes, 'BailianProvider', return_value=self.provider)]
        for p in self.patches: p.start()
        self.client = TestClient(app)
        self.client.cookies.set(COOKIE_NAME, self.fixture.token)

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches): p.stop()
        self.fixture.close()

    def run_turn(self, text='查看订单', request_id=None):
        with self.client.websocket_connect(f'/api/agent/ws?conversation_id={self.fixture.conversation}', headers={'origin':'http://localhost:5173'}) as ws:
            ws.receive_json()
            ws.send_json({'type':'chat.send','request_id': request_id or uuid4().hex, 'text':text,'speak':False})
            for _ in range(30):
                message = ws.receive_json()
                if message['type'] == 'turn.done': return message
        self.fail('turn did not finish')

    def test_websocket_trace_usage_replay_and_feedback_preserve_business(self):
        self.provider.last_usage = {'prompt_tokens':20,'completion_tokens':2,'total_tokens':22}
        before = self.fixture.snapshot()
        key = uuid4().hex
        self.assertEqual(self.run_turn(request_id=key)['status'], 'completed')
        self.assertEqual(self.run_turn(request_id=key)['status'], 'completed')
        overview = self.client.get('/api/agent/observability').json()
        self.assertEqual(overview['sampled_requests'], 2)
        self.assertEqual(overview['model_calls'], 1)
        self.assertEqual(overview['replayed_requests'], 1)
        self.assertEqual(overview['known_tokens'], 22)
        rows = self.client.get('/api/agent/observability/traces').json()['items']
        identifier = rows[0]['id']
        result = self.client.put(f'/api/agent/observability/traces/{identifier}/feedback', json={'rating':'unhelpful','note':'需要更清楚'})
        self.assertEqual(result.status_code, 200)
        detail = self.client.get(f'/api/agent/observability/traces/{identifier}').json()
        self.assertEqual(detail['feedback'], 'unhelpful')
        exported = self.client.get(f'/api/agent/observability/traces/{identifier}/evaluation-case').json()
        self.assertTrue(exported['requires_review'])
        self.assertEqual(self.fixture.snapshot(), before)

    def test_model_error_is_recorded_without_raw_exception(self):
        async def fail(*args): raise AgentError('provider_busy','secret-message')
        self.provider.complete = fail
        self.assertEqual(self.run_turn()['status'], 'failed')
        row = self.client.get('/api/agent/observability/traces').json()['items'][0]
        self.assertEqual(row['error_code'], 'provider_busy')
        detail = self.client.get(f"/api/agent/observability/traces/{row['id']}").json()
        self.assertIsNone(detail['token_usage'])
        self.assertNotIn('secret-message', json.dumps(detail))

    def test_tool_traces_and_account_isolation(self):
        self.provider.responses = [{'tool_calls':[{'id':'p', 'function':{'name':'search_products','arguments':'{"query":"评测键盘"}'}}]}]
        self.run_turn()
        row = self.client.get('/api/agent/observability/traces').json()['items'][0]
        detail = self.client.get(f"/api/agent/observability/traces/{row['id']}").json()
        tools = [e for e in detail['events'] if e['kind']=='tool']
        self.assertEqual(tools[0]['name'], 'search_products')
        self.assertEqual(tools[0]['status'], 'ok')
        self.app.dependency_overrides[require_current_user] = lambda: UserModel(id=999,username='other',password_hash='unused')
        self.assertEqual(self.client.get('/api/agent/observability').json()['sampled_requests'], 0)
        self.assertEqual(self.client.get(f"/api/agent/observability/traces/{row['id']}").status_code, 404)

    def test_unknown_usage_cost_and_redaction(self):
        self.assertEqual(usage_and_cost([{'kind':'model','usage':None}]), (None,None))
        events = [{'kind':'model','usage':{'prompt_tokens':1000,'completion_tokens':100}}]
        with patch.dict('os.environ', {'AGENT_INPUT_PRICE_PER_MILLION':'2','AGENT_OUTPUT_PRICE_PER_MILLION':'6'}):
            usage,cost = usage_and_cost(events)
        self.assertEqual(usage['total_tokens'],1100)
        self.assertEqual(Decimal(cost),Decimal('.002600'))
        text=redacted('phone 13800138000 email a@example.com token=secret sk-this-is-secret')
        for raw in ('13800138000','a@example.com','token=secret','sk-this-is-secret'):
            self.assertNotIn(raw,text)

    def test_completed_checkpoint_updates_preserve_feedback(self):
        trace = Trace(self.fixture.factory,self.fixture.conversation,1,'test-request','ChatSend','fake','prompt',[])
        trace.finish('running')
        with self.fixture.factory() as db:
            db.get(AgentTrace,trace.values['id']).feedback='helpful'
            db.commit()
        trace.finish('completed')
        with self.fixture.factory() as db:
            self.assertEqual(db.get(AgentTrace,trace.values['id']).feedback,'helpful')


class ProductToolTests(unittest.TestCase):
    def setUp(self): self.fixture = Fixture()
    def tearDown(self): self.fixture.close()

    def test_variant_filters_price_and_stock_are_not_product_aggregates(self):
        with self.fixture.factory() as db:
            original=db.scalar(select(ProductSKU))
            original.color='black'
            db.add_all([
                ProductSKU(product_id=1,code='EVAL-RED-M',color='red',size='M',price=Decimal('25'),stock=2,reserved=3,active=True),
                ProductSKU(product_id=1,code='EVAL-RED-L',color='red',size='L',price=Decimal('30'),stock=0,reserved=1,active=True),
                ProductSKU(product_id=1,code='EVAL-HIDDEN',color='blue',size='M',price=Decimal('99'),stock=100,reserved=0,active=False),
            ])
            db.commit()
            before=self.fixture.snapshot()
            result=search_products(db,query='评测键盘',color='RED',size='M')
            self.assertEqual(result['total'],1)
            row=result['items'][0]
            self.assertEqual(Decimal(row['price']),25)
            self.assertEqual((row['available_stock'],row['reserved_stock'],row['on_hand_stock']),(2,3,5))
            result=search_products(db,color='red',limit=1)
            self.assertTrue(result['truncated'])
            self.assertEqual(result['matched_price_range'],{'min':'25.00','max':'30.00'})
            self.assertEqual(search_products(db,sku_code='EVAL-HIDDEN')['total'],0)
            self.assertEqual(search_products(db,sku_code='EVAL-RED-L')['items'][0]['sellable'],False)
            self.assertEqual(search_products(db,color='purple')['items'],[])
            self.assertEqual(before,self.fixture.snapshot())


if __name__=='__main__': unittest.main()
