"""真实 MySQL 行锁验收；只创建随机隔离库，不使用业务表。"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, local
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import test_order_status as fixtures
from app.features.aftersales.models import AfterSale, RefundRecord
from app.features.aftersales.schemas import CaseCreate, RefundWrite, InspectionWrite
from app.features.aftersales.service import create_case, record_refund, inspect_return
from app.features.auth.models import UserModel
from app.features.inventory.schemas import AdjustmentCreate
from app.features.inventory.service import confirm_adjustment, create_adjustment
from app.features.operations.models import Movement, utcnow
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderStatus
from app.features.products.models import ProductModel
from app.features.shipping.fulfillment.schemas import ShipmentWrite
from app.features.shipping.fulfillment.service import save_shipment


@unittest.skipUnless(os.getenv("RUN_MYSQL_TESTS") == "1", "Set RUN_MYSQL_TESTS=1 for isolated MySQL tests")
class PlatformMySQLTests(fixtures.OrderFixtures, unittest.TestCase):
    setUpClass = classmethod(fixtures.MySQLConcurrencyTests.setUpClass.__func__)
    drop_test_database = classmethod(fixtures.MySQLConcurrencyTests.drop_test_database.__func__)

    def actor(self):
        with Session(self.engine, expire_on_commit=False) as db:
            actor = UserModel(username="ops-" + uuid4().hex[:20], password_hash="unused")
            db.add(actor)
            db.commit()
            return actor

    def overlapping_missing_key_requests(self, model, key_column, submit):
        """只同步首次查键，确定性覆盖空索引的间隙锁冲突，重试可正常继续。"""
        barrier = Barrier(2, timeout=10)
        state = local()
        def synchronize(connection, cursor, statement, parameters, context, many):
            if (statement.startswith('SELECT') and f'WHERE {model.__tablename__}.{key_column} =' in statement
                and not getattr(state, 'synchronized', False)):
                state.synchronized = True
                barrier.wait()
        event.listen(self.engine, 'after_cursor_execute', synchronize)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit, index) for index in range(2)]
                return [future.result(timeout=20) for future in futures]
        finally:
            event.remove(self.engine, 'after_cursor_execute', synchronize)

    def test_concurrent_case_creation_for_different_orders_survives_gap_lock_conflicts(self):
        orders = [self.seed_order()[0] for _ in range(2)]
        actor = self.actor()
        with Session(self.engine) as db:
            for oid in orders:
                OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
        def submit(index):
            with Session(self.engine) as db:
                return create_case(db, orders[index], CaseCreate(kind='refund', requested_amount='20.00',
                    reason='不同订单并发申请', idempotency_key=str(uuid4())), actor)
        results = self.overlapping_missing_key_requests(AfterSale, 'creation_key', submit)
        self.assertEqual({row['order_id'] for row in results}, set(orders))

    def test_concurrent_refunds_for_different_orders_survive_gap_lock_conflicts(self):
        orders = [self.seed_order()[0] for _ in range(2)]
        actor = self.actor()
        cases = []
        with Session(self.engine) as db:
            for oid in orders:
                OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
                cases.append(create_case(db, oid, CaseCreate(kind='refund', requested_amount='20.00',
                    reason='不同订单并发退款', idempotency_key=str(uuid4())), actor))
        def submit(index):
            with Session(self.engine) as db:
                return record_refund(db, orders[index], cases[index]['id'], RefundWrite(amount='15.00', reference='verified',
                    occurred_at=utcnow().isoformat()+'Z', expected_revision=1, idempotency_key=str(uuid4())), actor)
        results = self.overlapping_missing_key_requests(RefundRecord, 'idempotency_key', submit)
        self.assertEqual({row['order_id'] for row in results}, set(orders))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.sum(RefundRecord.amount)).where(RefundRecord.order_id.in_(orders))), 30)

    def test_earlier_snapshot_cannot_open_a_second_case(self):
        oid, _, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
        with Session(self.engine) as stale:
            self.assertIsNone(stale.scalar(select(AfterSale.id).where(AfterSale.order_id == oid)))
            with Session(self.engine) as fresh:
                create_case(fresh, oid, CaseCreate(kind='refund', requested_amount='5.00', reason='第一笔申请', idempotency_key=str(uuid4())), actor)
            with self.assertRaises(HTTPException) as raised:
                create_case(stale, oid, CaseCreate(kind='refund', requested_amount='5.00', reason='重叠请求', idempotency_key=str(uuid4())), actor)
            self.assertEqual(raised.exception.status_code, 409)

    def test_deadlock_retry_is_bounded_and_leaves_a_clean_session(self):
        actor = self.actor()
        payload = CaseCreate(kind='refund', requested_amount='5.00', reason='冲突重试', idempotency_key=str(uuid4()))
        failure = OperationalError('SELECT', {}, Exception(1213, 'deadlock'))
        with Session(self.engine) as db:
            db.scalar(select(AfterSale.id).limit(1))
            with patch('app.features.aftersales.service.lock_order', side_effect=failure) as locked:
                with self.assertRaises(HTTPException) as raised:
                    create_case(db, 1, payload, actor)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(locked.call_count, 3)
            self.assertFalse(db.in_transaction())

    def test_timeouts_and_connection_errors_are_never_automatically_retried(self):
        actor = self.actor()
        payload = CaseCreate(kind='refund', requested_amount='5.00', reason='不确定错误不重试', idempotency_key=str(uuid4()))
        for code in (1205, 2006):
            with self.subTest(code=code), Session(self.engine) as db:
                failure = OperationalError('SELECT', {}, Exception(code, 'not a deadlock'))
                with patch('app.features.aftersales.service.lock_order', side_effect=failure) as locked:
                    with self.assertRaises(OperationalError) as raised:
                        create_case(db, 1, payload, actor)
                self.assertIs(raised.exception, failure)
                self.assertEqual(locked.call_count, 1)

    def test_earlier_snapshot_cannot_reuse_a_refunded_balance(self):
        oid, _, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
        with Session(self.engine) as stale:
            self.assertIsNone(stale.scalar(select(RefundRecord.id).where(RefundRecord.order_id == oid)))
            with Session(self.engine) as fresh:
                case = create_case(fresh, oid, CaseCreate(kind='refund', requested_amount='15.00', reason='先退款', idempotency_key=str(uuid4())), actor)
                record_refund(fresh, oid, case['id'], RefundWrite(amount='15.00', reference='verified', occurred_at=utcnow().isoformat()+'Z', expected_revision=1, idempotency_key=str(uuid4())), actor)
            with self.assertRaises(HTTPException) as raised:
                create_case(stale, oid, CaseCreate(kind='refund', requested_amount='10.00', reason='超过余款', idempotency_key=str(uuid4())), actor)
            self.assertEqual(raised.exception.status_code, 409)

    def test_earlier_snapshot_replays_a_case_created_by_another_request(self):
        oid, _, _ = self.seed_order()
        actor = self.actor()
        payload = CaseCreate(kind='refund', requested_amount='5.00', reason='相同请求重试', idempotency_key=str(uuid4()))
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
        with Session(self.engine) as stale:
            self.assertIsNone(stale.scalar(select(AfterSale.id).where(AfterSale.order_id == oid)))
            with Session(self.engine) as fresh:
                case = create_case(fresh, oid, payload, actor)
            replay = create_case(stale, oid, payload, actor)
            self.assertEqual(replay, case)

    def test_earlier_snapshot_cannot_dispatch_with_a_new_open_case(self):
        oid, _, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            save_shipment(db, oid, ShipmentWrite(carrier_name='测试快递', tracking_number=f'TEST{oid:012d}', expected_revision=0), actor)
        with Session(self.engine) as stale:
            self.assertIsNone(stale.scalar(select(AfterSale.id).where(AfterSale.order_id == oid)))
            with Session(self.engine) as fresh:
                create_case(fresh, oid, CaseCreate(kind='refund', requested_amount='5.00', reason='暂停发货', idempotency_key=str(uuid4())), actor)
            _, error = OrderCRUD(stale).update_status(oid, OrderStatus.IN_DELIVERY, actor)
            self.assertIsNotNone(error)
            self.assertIn('售后', error)

    def test_earlier_snapshot_cannot_request_already_returned_items(self):
        oid, _, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            save_shipment(db, oid, ShipmentWrite(carrier_name='测试快递', tracking_number=f'TEST{oid:012d}', expected_revision=0), actor)
            order, error = OrderCRUD(db).update_status(oid, OrderStatus.IN_DELIVERY, actor)
            self.assertIsNone(error)
            item_id = order.items[0].id
        with Session(self.engine) as stale:
            self.assertIsNone(stale.scalar(select(AfterSale.id).where(AfterSale.order_id == oid)))
            with Session(self.engine) as fresh:
                case = create_case(fresh, oid, CaseCreate(kind='return_refund', requested_amount='5.00', reason='退回全部两件',
                    items=[{'order_item_id': item_id, 'quantity': 2}], idempotency_key=str(uuid4())), actor)
                case = inspect_return(fresh, oid, case['id'], InspectionWrite(expected_revision=1, note='两件均合格',
                    items=[{'order_item_id': item_id, 'accepted_quantity': 2, 'damaged_quantity': 0}]), actor)
                record_refund(fresh, oid, case['id'], RefundWrite(amount='5.00', reference='verified', occurred_at=utcnow().isoformat()+'Z',
                    expected_revision=case['revision'], idempotency_key=str(uuid4())), actor)
            with self.assertRaises(HTTPException) as raised:
                create_case(stale, oid, CaseCreate(kind='return_refund', requested_amount='5.00', reason='重复退货',
                    items=[{'order_item_id': item_id, 'quantity': 1}], idempotency_key=str(uuid4())), actor)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn('数量', raised.exception.detail)

    def test_two_refund_requests_cannot_spend_the_same_balance(self):
        oid, pid, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            OrderCRUD(db).update_status(oid, OrderStatus.PAID_PENDING_SHIPMENT, actor)
            case = create_case(db, oid, CaseCreate(kind="refund", requested_amount="20.00", reason="并发核对", idempotency_key=str(uuid4())), actor)
        barrier = Barrier(2, timeout=10)
        def submit():
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait()
                try:
                    record_refund(db, oid, case["id"], RefundWrite(amount="15.00", reference="verified", occurred_at=utcnow().isoformat() + "Z", expected_revision=1, idempotency_key=str(uuid4())), actor)
                    return True
                except HTTPException as error:
                    db.rollback()
                    self.assertEqual(error.status_code, 409, error.detail)
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit), pool.submit(submit)]
            self.assertEqual(sum(f.result(timeout=20) for f in futures), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.sum(RefundRecord.amount)).where(RefundRecord.order_id == oid)), 15)

    def test_two_adjustment_drafts_cannot_overwrite_the_same_inventory_version(self):
        oid, pid, _ = self.seed_order()
        actor = self.actor()
        with Session(self.engine) as db:
            drafts = [create_adjustment(db, AdjustmentCreate(kind="damage", reason="实物损坏", items=[{"product_id": pid, "available_delta": -1}], idempotency_key=str(uuid4())), actor) for _ in range(2)]
        barrier = Barrier(2, timeout=10)
        def submit(draft):
            with Session(self.engine, expire_on_commit=False) as db:
                barrier.wait()
                try:
                    confirm_adjustment(db, draft["id"], actor)
                    return True
                except HTTPException as error:
                    db.rollback()
                    self.assertEqual(error.status_code, 409)
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, draft) for draft in drafts]
            self.assertEqual(sum(f.result(timeout=20) for f in futures), 1)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ProductModel, pid).stock, 7)
            self.assertEqual(db.scalar(select(func.count()).select_from(Movement).where(Movement.product_id == pid, Movement.movement_type == "stock_damage")), 1)
