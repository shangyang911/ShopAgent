"""新功能验收：业务待办、批量事务与客户经营事实，全部使用隔离数据库。"""
from datetime import timedelta
from uuid import uuid4
import unittest
from unittest.mock import patch
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import test_core_operations as fixtures
from test_order_status import OrderFixtures
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.operations.models import OrderFacts, Receipt, StockAlert, utcnow
from app.features.operations.inventory import evaluate_alert
from app.features.orders.models import OrderModel, OrderStatus
from app.features.products.models import ProductModel
from app.features.products.favorites.models import ProductFavorite
from app.features.products.batch.models import ProductBatchLog
from app.features.products.batch.schemas import BatchApply
from app.features.products.batch import service as batch_service
from app.features.aftersales.models import AfterSale, RefundRecord
from app.main import app


class OperationalFeatureTests(OrderFixtures, unittest.TestCase):
    setUp = fixtures.CoreOperationsTests.setUp
    tearDown = fixtures.CoreOperationsTests.tearDown
    pay = fixtures.CoreOperationsTests.pay

    def product(self, stock=10):
        with Session(self.engine, expire_on_commit=False) as db:
            row = ProductModel(name="批量测试商品", category="测试", price=10, stock=stock,
                status="Active", image_url="/test.png", color="", shipping="")
            db.add(row)
            db.flush()
            evaluate_alert(db, row)
            db.commit()
            return row.id

    def preview(self, ids, action="deactivate"):
        response = self.client.post('/api/products/batch/preview', json={"ids": ids, "action": action})
        self.assertEqual(response.status_code, 200, response.text)
        return {"ids": ids, "action": action, "snapshot": response.json()["snapshot"], "idempotency_key": str(uuid4())}

    def test_notification_read_is_per_account_and_does_not_close_risk(self):
        self.product(stock=0)
        result = self.client.get('/api/notifications').json()
        self.assertEqual(result['unread_count'], 1)
        item = result['items'][0]
        for _ in range(2):
            self.assertEqual(self.client.post('/api/notifications/read', json={"keys": [item['key']]}).status_code, 204)
        result = self.client.get('/api/notifications').json()
        self.assertEqual(result['unread_count'], 0)
        self.assertEqual(result['total'], 1)
        self.assertIsNotNone(result['items'][0]['read_at'])
        with Session(self.engine, expire_on_commit=False) as db:
            self.assertEqual(db.scalar(select(StockAlert.alert_status)), 'active')
            other = UserModel(username='another', password_hash='unused')
            db.add(other); db.commit()
        app.dependency_overrides[require_current_user] = lambda: other
        self.assertEqual(self.client.get('/api/notifications').json()['unread_count'], 1)

    def test_recovered_stock_disappears_and_new_occurrence_is_unread(self):
        pid = self.product(stock=0)
        old_key = self.client.get('/api/notifications').json()['items'][0]['key']
        self.client.post('/api/notifications/read', json={"keys": [old_key]})
        with Session(self.engine) as db:
            row = db.get(ProductModel, pid); row.stock = 20
            evaluate_alert(db, row); db.commit()
        self.assertEqual(self.client.get('/api/notifications').json()['total'], 0)
        with Session(self.engine) as db:
            row = db.get(ProductModel, pid); row.stock = 0
            evaluate_alert(db, row); db.commit()
        result = self.client.get('/api/notifications').json()
        self.assertEqual(result['unread_count'], 1)
        self.assertNotEqual(result['items'][0]['key'], old_key)

    def test_mark_page_read_then_all_pages_read_keeps_business_tasks(self):
        for _ in range(25):
            self.product(stock=0)
        first = self.client.get('/api/notifications').json()
        self.assertEqual((first['active_count'], first['unread_count'], len(first['items'])), (25, 25, 20))
        self.client.post('/api/notifications/read', json={"keys": [item['key'] for item in first['items']]})
        after_page = self.client.get('/api/notifications').json()
        self.assertEqual(after_page['unread_count'], 5)
        self.assertTrue(all(item['read_at'] for item in after_page['items']))
        for _ in range(2):
            self.assertEqual(self.client.post('/api/notifications/read-all').status_code, 204)
        all_read = self.client.get('/api/notifications?category=stock&state=unread').json()
        self.assertEqual((all_read['active_count'], all_read['unread_count'], all_read['total']), (25, 0, 0))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(StockAlert).where(StockAlert.alert_status == 'active')), 25)
        # 新出现的业务风险仍应显示红色未读角标，不能被历史的“全部已读”屏蔽。
        self.product(stock=0)
        self.assertEqual(self.client.get('/api/notifications').json()['unread_count'], 1)

    def test_mark_all_is_account_scoped_and_empty_lists_are_safe(self):
        self.assertEqual(self.client.post('/api/notifications/read-all').status_code, 204)
        self.product(stock=0)
        self.client.post('/api/notifications/read-all')
        with Session(self.engine, expire_on_commit=False) as db:
            other = UserModel(username='other-read-all', password_hash='unused')
            db.add(other); db.commit()
        app.dependency_overrides[require_current_user] = lambda: other
        self.assertEqual(self.client.get('/api/notifications').json()['unread_count'], 1)

    def test_overdue_and_aftersale_are_live_filtered_and_paginated(self):
        oid, _, _ = self.seed_order()
        self.pay(oid)
        with Session(self.engine) as db:
            db.get(OrderFacts, oid).paid_at = utcnow() - timedelta(hours=25)
            case = AfterSale(order_id=oid, kind='refund', status='open', requested_amount=5,
                reason='测试', revision=1, creation_key=str(uuid4()), creation_hash='test', created_by='test')
            db.add(case); db.commit()
        result = self.client.get('/api/notifications?page_size=1').json()
        self.assertEqual((result['total'], len(result['items'])), (2, 1))
        self.assertEqual(self.client.get('/api/notifications?category=shipment').json()['total'], 1)
        with Session(self.engine) as db:
            db.get(OrderModel, oid).status = OrderStatus.IN_DELIVERY
            case = db.scalar(select(AfterSale)); case.status = 'closed'; db.commit()
        self.assertEqual(self.client.get('/api/notifications').json()['total'], 0)
        self.assertEqual(self.client.post('/api/notifications/read', json={"keys": ['shipment:9999']}).status_code, 409)

    def test_batch_preview_is_readonly_and_apply_retries_only_log_once(self):
        first, second = self.product(), self.product()
        payload = self.preview([first, second, first])
        with Session(self.engine) as db:
            self.assertEqual(db.get(ProductModel, first).status, 'Active')
        for _ in range(2):
            response = self.client.post('/api/products/batch/apply', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['count'], 2)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ProductModel, first).status, 'Inactive')
            self.assertEqual(db.get(ProductModel, first).stock, 10)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductBatchLog)), 1)

    def test_batch_conflicts_do_not_partially_update_other_products(self):
        first, second = self.product(), self.product()
        payload = self.preview([first, second])
        with Session(self.engine) as db:
            db.get(ProductModel, second).status = 'Archived'; db.commit()
        self.assertEqual(self.client.post('/api/products/batch/apply', json=payload).status_code, 409)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ProductModel, first).status, 'Active')
        self.assertEqual(self.client.post('/api/products/batch/preview', json={"ids": [first, 99999], "action": 'archive'}).status_code, 409)
        self.assertEqual(self.client.post('/api/products/batch/preview', json={"ids": [], "action": 'archive'}).status_code, 422)

    def test_batch_commit_failure_rolls_back_all_state_and_audit(self):
        first, second = self.product(), self.product()
        payload = BatchApply(**self.preview([first, second]))
        with Session(self.engine) as db:
            with patch.object(db, 'commit', side_effect=RuntimeError('commit failed')):
                with self.assertRaises(RuntimeError): batch_service.apply(db, self.actor, payload)
            db.rollback()
        with Session(self.engine) as db:
            self.assertEqual([db.get(ProductModel, pid).status for pid in (first, second)], ['Active', 'Active'])
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductBatchLog)), 0)

    def test_batch_favorites_can_be_added_and_removed(self):
        pid = self.product()
        for action, expected in [('favorite', 1), ('unfavorite', 0)]:
            payload = self.preview([pid], action)
            self.assertEqual(self.client.post('/api/products/batch/apply', json=payload).status_code, 200)
            with Session(self.engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(ProductFavorite)), expected)

    def test_customer_summary_uses_receipts_and_refunds_without_multiplication(self):
        oid, _, _ = self.seed_order((2, 3))
        self.pay(oid)
        with Session(self.engine) as db:
            cid = db.get(OrderModel, oid).customer_id
            case = AfterSale(order_id=oid, kind='refund', status='closed', requested_amount=7,
                reason='测试', revision=1, creation_key=str(uuid4()), creation_hash='test', created_by='test')
            db.add(case); db.flush()
            for amount in (3, 4):
                db.add(RefundRecord(case_id=case.id, order_id=oid, amount=amount, reference='test',
                    occurred_at=utcnow(), recorded_by='test', request_hash='test', idempotency_key=str(uuid4())))
            db.commit()
        response = self.client.get(f'/api/customers/{cid}')
        self.assertEqual(response.status_code, 200, response.text)
        summary = response.json()['summary']
        self.assertEqual(float(summary['confirmed_amount']), 50)
        self.assertEqual(float(summary['refunded_amount']), 7)
        self.assertEqual(float(summary['net_confirmed_amount']), 43)
        self.assertEqual(summary['paid_order_count'], 1)
        self.assertEqual(len(response.json()['orders']['items']), 1)
        self.assertEqual(response.json()['common_products'][0]['quantity'], 5)

    def test_customer_profile_revision_and_tags(self):
        oid, _, _ = self.seed_order()
        with Session(self.engine) as db: cid = db.get(OrderModel, oid).customer_id
        path = f'/api/customers/{cid}/profile'
        payload = {"tags": [' 老客户 ', '老客户', ''], "note": ' 跟进 ', "expected_revision": 0}
        response = self.client.put(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"tags": ['老客户'], "note": '跟进', "revision": 1})
        self.assertEqual(self.client.put(path, json=payload).status_code, 409)
        self.assertEqual(self.client.get('/api/customers/999999').status_code, 404)
        self.assertEqual(self.client.get(f'/api/customers/{cid}?page=0').status_code, 422)

    def test_new_endpoints_require_authentication(self):
        app.dependency_overrides.pop(require_current_user)
        for path in ['/api/notifications', '/api/customers/1']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/products/batch/preview', json={"ids": [1], "action": 'archive'}).status_code, 401)
        self.assertEqual(self.client.post('/api/notifications/read-all').status_code, 401)
