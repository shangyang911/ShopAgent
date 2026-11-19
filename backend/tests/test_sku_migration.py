"""Historical SKU backfill runs once, without changing inventory balances."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest

from sqlalchemy import create_engine, delete, event, func, select, update
from sqlalchemy.orm import Session

from app.db.base import Base
import app.db.session  # noqa: F401 - register all application tables.
from app.features.operations.models import Movement, Setting
from app.features.orders.models import OrderItemModel
from app.features.products.models import ProductImage, ProductModel, ProductSKU
from app.features.products.sku_inventory import initialize_product_skus
import test_order_status as order_fixtures
from test_order_status import OrderFixtures


class SkuMigrationTests(OrderFixtures, unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.order_id, self.product_id, _ = self.seed_order()
        with Session(self.engine) as db:
            db.execute(update(OrderItemModel).values(sku_id=None, sku_code=None, sku_label=None))
            db.execute(update(Movement).values(sku_id=None, sku_code_snapshot=None))
            db.execute(delete(ProductSKU))
            db.commit()

    def test_first_run_preserves_balances_and_backfills_historical_snapshots(self):
        with Session(self.engine, autoflush=False) as db:
            initialize_product_skus(db)
            sku = db.scalar(select(ProductSKU))
            self.assertEqual((sku.stock, sku.reserved), (8, 2))
            self.assertEqual(db.get(ProductModel, self.product_id).stock, 8)
            item = db.scalar(select(OrderItemModel))
            self.assertEqual(item.sku_id, sku.id)
            self.assertEqual(item.sku_code, sku.code)
            self.assertEqual(item.unit_price, 10)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductImage)), 1)
            self.assertIsNotNone(db.get(Setting, 'migration:product_skus:v1'))

    def test_completed_migration_does_not_scan_or_update_business_tables(self):
        with Session(self.engine) as db:
            initialize_product_skus(db)
        statements = []
        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement.lower())
        event.listen(self.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', record)
        with Session(self.engine) as db:
            initialize_product_skus(db)
        for table in ('products', 'product_skus', 'product_images', 'order_items',
                      'inventory_movements', 'inbound_receipt_lines', 'stock_adjustment_lines'):
            self.assertFalse(any(table in sql for sql in statements), table)

    def test_failure_rolls_back_backfill_and_allows_retry(self):
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('UPDATE ORDER_ITEMS'):
                raise RuntimeError('Interrupted migration')
        event.listen(self.engine, 'before_cursor_execute', fail)
        try:
            with Session(self.engine, autoflush=False) as db:
                with self.assertRaisesRegex(RuntimeError, 'Interrupted migration'):
                    initialize_product_skus(db)
                db.rollback()
        finally:
            event.remove(self.engine, 'before_cursor_execute', fail)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Setting, 'migration:product_skus:v1'))
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductSKU)), 0)
            initialize_product_skus(db)
            self.assertIsNotNone(db.scalar(select(OrderItemModel.sku_id)))
            self.assertEqual(db.scalar(select(ProductSKU.reserved)), 2)


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Set RUN_MYSQL_TESTS=1 for isolated MySQL tests')
class SkuMigrationMySQLTests(OrderFixtures, unittest.TestCase):
    setUpClass = classmethod(order_fixtures.MySQLConcurrencyTests.setUpClass.__func__)
    drop_test_database = classmethod(order_fixtures.MySQLConcurrencyTests.drop_test_database.__func__)

    def test_concurrent_starters_backfill_once(self):
        order_id, product_id, _ = self.seed_order()
        with Session(self.engine) as db:
            db.execute(update(OrderItemModel).values(sku_id=None, sku_code=None, sku_label=None))
            db.execute(update(Movement).values(sku_id=None, sku_code_snapshot=None))
            db.execute(delete(ProductSKU))
            db.commit()
        barrier = Barrier(2, timeout=10)
        updates = []
        def observe(connection, cursor, statement, parameters, context, executemany):
            sql = statement.upper()
            if sql.startswith('SELECT OPERATION_SETTINGS.') and 'FOR UPDATE' not in sql:
                barrier.wait()
            if sql.startswith('UPDATE ORDER_ITEMS '):
                updates.append(statement)
        event.listen(self.engine, 'after_cursor_execute', observe)
        def start():
            with Session(self.engine, autoflush=False) as db:
                initialize_product_skus(db)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(start) for _ in range(2)]
                for future in futures:
                    future.result(timeout=30)
        finally:
            event.remove(self.engine, 'after_cursor_execute', observe)
        self.assertEqual(len(updates), 1)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.scalar(select(OrderItemModel.sku_id)))
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductSKU)), 1)
            self.assertEqual(db.scalar(select(ProductSKU.reserved)), 2)
            self.assertEqual(db.get(ProductModel, product_id).stock, 8)


if __name__ == '__main__':
    unittest.main()
