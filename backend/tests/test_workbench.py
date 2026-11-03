"""Workbench acceptance tests run only against the isolated fixture database."""
import csv
from datetime import datetime
from io import StringIO
import unittest

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.operations.models import Movement, OrderEvent, OrderFacts, PrintJob
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.products.models import ProductModel
import test_core_operations as core_fixtures
from test_order_status import OrderFixtures


class WorkbenchTests(OrderFixtures, unittest.TestCase):
    setUp = core_fixtures.CoreOperationsTests.setUp
    tearDown = core_fixtures.CoreOperationsTests.tearDown
    count = core_fixtures.CoreOperationsTests.count
    pay = core_fixtures.CoreOperationsTests.pay
    stock_values = core_fixtures.CoreOperationsTests.stock_values

    def test_keyword_exact_order_and_phone_snapshot_search_and_status_counts(self):
        first, pid, _ = self.seed_order()
        second, _, _ = self.seed_order()
        self.pay(second)
        number = self.client.get(f'/api/orders/{first}').json()['order_number']
        with Session(self.engine) as db:
            db.get(OrderModel, second).recipient_name = number
            db.get(ProductModel, pid).name = '当前改名不影响旧订单'
            db.commit()
        exact = self.client.get('/api/orders', params={'keyword': f' {number} '}).json()
        self.assertEqual([row['id'] for row in exact['items']], [first])
        self.assertEqual(self.client.get('/api/orders', params={'keyword': '10000'}).json()['total'], 2)
        self.assertEqual(self.client.get('/api/orders', params={'keyword': '1000'}).json()['total'], 0)
        self.assertEqual(self.client.get('/api/orders', params={'keyword': '当前改名'}).json()['total'], 0)
        self.assertEqual(self.client.get('/api/orders', params={'keyword': 'Regression product'}).json()['total'], 2)
        self.assertEqual(self.client.get('/api/orders', params={'keyword': '%'}).json()['total'], 0)
        selected = self.client.get('/api/orders', params={'statuses': ['pending_payment'], 'keyword': 'Regression product'}).json()
        self.assertEqual(selected['total'], 1)
        self.assertEqual(selected['status_counts'], {'pending_payment': 1, 'paid_pending_shipment': 1})
        combined = self.client.get('/api/orders', params={'statuses': ['pending_payment', 'paid_pending_shipment'], 'min_total': '20.00', 'max_total': '20.00'}).json()
        self.assertEqual(combined['total'], 2)

    def test_stable_sort_and_unknown_dates_do_not_fabricate_matches(self):
        ids = [self.seed_order()[0] for _ in range(3)]
        with Session(self.engine) as db:
            for oid in ids:
                db.get(OrderFacts, oid).created_at = datetime(2026, 10, 1, 1)
            db.get(OrderFacts, ids[0]).created_at = None
            db.commit()
        newest = self.client.get('/api/orders').json()
        self.assertEqual([row['id'] for row in newest['items']], [ids[2], ids[1], ids[0]])
        oldest = self.client.get('/api/orders', params={'sort': 'created_asc'}).json()
        self.assertEqual([row['id'] for row in oldest['items']], [ids[1], ids[2], ids[0]])
        unknown = self.client.get('/api/orders', params={'unknown_time': True}).json()
        self.assertEqual([row['id'] for row in unknown['items']], [ids[0]])
        known = self.client.get('/api/orders', params={'start_at': '2026-10-01T00:00:00Z', 'end_at': '2026-10-02T00:00:00Z'}).json()
        self.assertEqual(known['total'], 2)
        response = self.client.get('/api/orders/export', params={'unknown_time': True})
        self.assertEqual(response.status_code, 200, response.text)
        row = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))[0]
        self.assertEqual(row['created_at'], '')

    def test_export_uses_all_filters_and_includes_more_than_one_page(self):
        ids = [self.seed_order()[0] for _ in range(23)]
        for oid in ids[-2:]:
            response = self.client.patch(f'/api/orders/{oid}/status', json={'status': 'paid_pending_shipment'})
            self.assertEqual(response.status_code, 200, response.text)
        params = {'keyword': 'Regression product', 'statuses': ['pending_payment'], 'min_total': '20.00', 'max_total': '20.00'}
        listed = self.client.get('/api/orders', params=params).json()
        self.assertEqual(len(listed['items']), 20)
        self.assertEqual(listed['total'], 21)
        response = self.client.get('/api/orders/export', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        rows = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 21)
        self.assertTrue(all(row['status'] == 'pending_payment' and row['total'] == '20.00' for row in rows))
        self.assertEqual({int(row['order_id']) for row in rows}, set(ids[:-2]))
        for path in ('/api/orders', '/api/orders/export'):
            response = self.client.get(path, params={'min_total': '21.00', 'max_total': '20.00'})
            self.assertEqual(response.status_code, 422)
            self.assertIn('最低金额', response.json()['detail'])
        from app.features.auth.router import require_current_user
        from app.main import app
        app.dependency_overrides.pop(require_current_user)
        self.assertEqual(self.client.get('/api/orders/export').status_code, 401)

    def test_event_correction_appends_once_preserves_original_and_business_facts(self):
        oid, pid, _ = self.seed_order()
        self.pay(oid)
        original = self.client.get(f'/api/order-operations/orders/{oid}/events').json()['items'][0]
        self.assertEqual(original['changed_fields']['status']['after'], 'paid_pending_shipment')
        self.assertIsNotNone(original['related_receipt_id'])
        self.assertTrue(original['correlation_id'])
        before = self.client.get(f'/api/orders/{oid}').json()
        url = f"/api/order-operations/orders/{oid}/events/{original['id']}/corrections"
        payload = {'content': '补充收款说明', 'reason': '原说明不完整', 'idempotency_key': 'correction-acceptance-1'}
        first = self.client.post(url, json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        replay = self.client.post(url, json=payload)
        self.assertEqual(first.json()['id'], replay.json()['id'])
        self.assertEqual(self.client.post(url, json={**payload, 'content': '篡改内容'}).status_code, 409)
        self.assertEqual(self.client.post(url, json={**payload, 'reason': '  ', 'idempotency_key': 'new-correction-key'}).status_code, 422)
        events = self.client.get(f'/api/order-operations/orders/{oid}/events').json()
        self.assertEqual(events['total'], 3)
        self.assertEqual(events['items'][0]['event_type'], 'correction_added')
        self.assertEqual(events['items'][0]['details']['corrects_event_id'], original['id'])
        self.assertEqual(events['items'][1]['summary'], original['summary'])
        after = self.client.get(f'/api/orders/{oid}').json()
        for field in ('paid_at', 'created_at', 'status', 'dispatch_started_at', 'completed_at'):
            self.assertEqual(before[field], after[field])
        self.assertEqual(self.stock_values(pid), (8, 2, 10))

    def test_event_pages_are_reverse_sequence_and_snapshot_authors_are_immutable(self):
        oid, _, _ = self.seed_order()
        for index in range(24):
            response = self.client.post(f'/api/order-operations/orders/{oid}/notes', json={
                'content': f'内部备注 {index}', 'idempotency_key': f'pagination-note-{index:02d}'})
            self.assertEqual(response.status_code, 200, response.text)
        first = self.client.get(f'/api/order-operations/orders/{oid}/events').json()
        second = self.client.get(f'/api/order-operations/orders/{oid}/events?page=2').json()
        self.assertEqual(first['total'], 25)
        self.assertEqual([row['sequence'] for row in first['items']], list(range(25, 5, -1)))
        self.assertEqual([row['sequence'] for row in second['items']], list(range(5, 0, -1)))
        with Session(self.engine) as db:
            from app.features.auth.models import UserModel
            db.get(UserModel, self.actor.id).username = 'renamed-admin'
            db.commit()
        again = self.client.get(f'/api/order-operations/orders/{oid}/events').json()
        self.assertEqual(again['items'][0]['actor_name_snapshot'], 'actual-admin')

    def test_summary_groups_snapshots_and_live_validation_rejects_cancelled_order(self):
        first, pid, payload = self.seed_order()
        with Session(self.engine) as db:
            row, error = OrderCRUD(db).create_order(payload)
            self.assertIsNone(error)
            second = row.id
        self.pay(first)
        self.client.patch(f'/api/orders/{second}/status', json={'status': 'paid_pending_shipment'})
        before = self.stock_values(pid)
        movement_count = self.count(Movement)
        job = self.client.post('/api/order-operations/print-jobs', json={
            'order_ids': [first, second], 'idempotency_key': 'summary-test-print', 'layout': 'summary'}).json()
        html = self.client.get(f"/api/order-operations/print-jobs/{job['id']}/html?layout=summary").text
        self.assertIn('合并拣货汇总单', html)
        self.assertEqual(html.count('<template class="document-source">'), 1)
        self.assertIn('4 件', html)
        self.assertIn('printRegisteredDocument', html)
        self.assertIn('body:not([data-print-approved=true]) #pages{display:none}', html)
        self.assertEqual(self.count(Movement), movement_count)
        self.assertEqual(self.stock_values(pid), before)
        self.assertEqual(self.client.get(f"/api/order-operations/print-jobs/{job['id']}/validate").status_code, 200)
        self.client.patch(f'/api/orders/{first}/status', json={'status': 'cancelled', 'reason': '状态冲突验收'})
        self.assertEqual(self.client.get(f"/api/order-operations/print-jobs/{job['id']}/validate").status_code, 409)
        active_doc = next(doc for doc in job['documents'] if doc['order_id'] == second)
        self.assertEqual(self.client.get(f"/api/order-operations/print-jobs/{job['id']}/validate?document_id={active_doc['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/order-operations/print-jobs/{job['id']}/validate?document_id=999999").status_code, 404)
        self.assertEqual(self.count(PrintJob), 1)
