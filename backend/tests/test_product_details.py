"""Product-detail acceptance: real API and inventory services in an isolated database."""
from decimal import Decimal
import unittest
from unittest.mock import patch
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

import test_fulfillment as fixtures
from test_order_status import OrderFixtures
from app.features.products.models import ProductModel, ProductImage, ProductSKU
from app.features.products.sku_inventory import initialize_product_skus
from app.features.operations.models import Movement, Reservation
from app.features.orders.models import OrderItemModel


class ProductDetailTests(OrderFixtures, unittest.TestCase):
    setUp = fixtures.FulfillmentTests.setUp
    tearDown = fixtures.FulfillmentTests.tearDown
    paid_order = fixtures.FulfillmentTests.paid_order
    record = fixtures.FulfillmentTests.record
    dispatch = fixtures.FulfillmentTests.dispatch
    stock = fixtures.FulfillmentTests.stock

    def product(self):
        oid, pid = self.paid_order()
        return oid, pid, self.client.get(f'/api/products/{pid}').json()

    def variant(self, pid, **changes):
        payload = dict(code=f'P{pid}-BLUE-M', color='Blue', size='M', price='15.00', **changes)
        response = self.client.post(f'/api/products/{pid}/skus', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['skus'][-1]

    def allocate(self, pid, source, target, quantity=3, key='allocate-test-123'):
        return self.client.post(f'/api/products/{pid}/sku-stock-transfer', json={
            'from_sku_id':source,'to_sku_id':target,'quantity':quantity,
            'reason':'核对实物后分配颜色尺寸库存','idempotency_key':key})

    def test_detail_and_default_sku_preserve_inventory_and_order_price(self):
        oid, pid, detail = self.product()
        self.assertEqual(detail['skus'][0]['available_quantity'], 8)
        self.assertEqual(detail['skus'][0]['reserved_quantity'], 2)
        self.assertEqual(detail['skus'][0]['on_hand_quantity'], 10)
        self.assertEqual(self.stock(pid), (8,2,10))
        order = self.client.get(f'/api/orders/{oid}').json()
        self.assertEqual(order['items'][0]['sku_id'], detail['skus'][0]['id'])
        self.assertEqual(Decimal(order['items'][0]['unit_price']), Decimal('10'))

    def test_new_variant_has_zero_stock_and_allocation_is_idempotent(self):
        oid, pid, detail = self.product()
        source=detail['skus'][0]['id']
        variant=self.variant(pid)
        self.assertEqual(variant['stock'],0)
        first=self.allocate(pid,source,variant['id'])
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.allocate(pid,source,variant['id']).status_code,200)
        self.assertEqual(self.stock(pid),(8,2,10))
        byid={row['id']:row for row in first.json()['skus']}
        self.assertEqual(byid[source]['stock'],5)
        self.assertEqual(byid[variant['id']]['stock'],3)
        self.assertEqual(self.allocate(pid,source,variant['id'],quantity=4).status_code,409)
        self.assertEqual(self.allocate(pid,source,variant['id'],quantity=6,key='too-much-allocation').status_code,409)

    def test_multiple_variants_require_explicit_selection_and_freeze_price(self):
        oid,pid,detail=self.product()
        variant=self.variant(pid)
        self.allocate(pid,detail['skus'][0]['id'],variant['id'])
        with Session(self.engine) as db:
            from app.features.orders.models import OrderModel
            original=db.get(OrderModel,oid)
            customer,address=original.customer_id,original.address_id
        body={'customer_id':customer,'address_id':address,'items':[{'product_id':pid,'quantity':2}]}
        self.assertEqual(self.client.post('/api/orders',json=body).status_code,409)
        body['items'][0]['sku_id']=variant['id']
        response=self.client.post('/api/orders',json=body)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(Decimal(response.json()['total']),Decimal('30'))
        self.assertEqual(response.json()['items'][0]['sku_label'],'Blue / M')
        current=next(row for row in self.client.get(f'/api/products/{pid}').json()['skus'] if row['id']==variant['id'])
        changed=self.client.put(f"/api/products/{pid}/skus/{variant['id']}",json={
            'code':current['code'],'color':current['color'],'size':current['size'],'active':True,
            'price':'99.00','expected_revision':current['revision']})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(Decimal(self.client.get(f"/api/orders/{response.json()['id']}").json()['total']),Decimal('30.00'))
        self.assertEqual(self.stock(pid),(6,4,10))
        cancelled=self.client.patch(f"/api/orders/{response.json()['id']}/status",json={'status':'cancelled','reason':'SKU cancellation'})
        self.assertEqual(cancelled.status_code,200,cancelled.text)
        result=self.client.get(f'/api/products/{pid}').json()
        self.assertEqual(next(s for s in result['skus'] if s['id']==variant['id'])['stock'],3)

    def test_same_product_different_skus_remain_separate_order_lines(self):
        oid,pid,detail=self.product()
        variant=self.variant(pid)
        self.allocate(pid,detail['skus'][0]['id'],variant['id'])
        order=self.client.get(f'/api/orders/{oid}').json()
        body={'customer_id':order['customer_id'],'address_id':order['address_id'],'items':[
            {'product_id':pid,'sku_id':detail['skus'][0]['id'],'quantity':1},
            {'product_id':pid,'sku_id':variant['id'],'quantity':2},
            {'product_id':pid,'sku_id':variant['id'],'quantity':1}]}
        response=self.client.post('/api/orders',json=body)
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(len(response.json()['items']),2)
        self.assertEqual(Decimal(response.json()['total']),Decimal('55'))
        new_oid=response.json()['id']
        self.assertEqual(self.client.patch(f'/api/orders/{new_oid}/status',json={'status':'paid_pending_shipment'}).status_code,200)
        from app.features.operations.printing import request_print, render_print
        from app.features.operations.orders_router import PrintPayload
        with Session(self.engine,expire_on_commit=False) as db:
            job=request_print(db,PrintPayload(order_ids=[new_oid],layout='summary',idempotency_key='sku-summary-print'),self.actor)
            html=render_print(db,job,layout='summary')
            self.assertIn(variant['code'],html)
            self.assertIn(detail['skus'][0]['code'],html)
            self.assertIn('2 项商品快照',html)

    def test_sku_validation_conflicts_and_reserved_stock_cannot_be_disabled(self):
        oid,pid,detail=self.product()
        variant=self.variant(pid)
        duplicate=self.client.post(f'/api/products/{pid}/skus',json={'code':'DIFFERENT-CODE','color':'Blue','size':'M','price':'9'})
        self.assertEqual(duplicate.status_code,409)
        default=detail['skus'][0]
        body={key:default[key] for key in ('code','color','size','price')}
        body.update(active=False,expected_revision=default['revision'])
        self.assertEqual(self.client.put(f"/api/products/{pid}/skus/{default['id']}",json=body).status_code,409)
        body.update(active=True,expected_revision=999)
        self.assertEqual(self.client.put(f"/api/products/{pid}/skus/{default['id']}",json=body).status_code,409)

    def test_sku_inbound_updates_only_selected_variant_and_preserves_other_stock(self):
        oid,pid,detail=self.product()
        variant=self.variant(pid)
        response=self.client.post('/api/inventory/inbounds',json={'items':[{'product_id':pid,'sku_id':variant['id'],'quantity':4}], 'idempotency_key':'sku-inbound-request'})
        self.assertEqual(response.status_code,201,response.text)
        rid=response.json()['id']
        response=self.client.post(f'/api/inventory/inbounds/{rid}/confirm')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.post(f'/api/inventory/inbounds/{rid}/confirm').status_code,200)
        detail=self.client.get(f'/api/products/{pid}').json()
        self.assertEqual(detail['skus'][0]['stock'],8)
        self.assertEqual(detail['skus'][1]['stock'],4)
        self.assertEqual(self.stock(pid),(12,2,14))

    def test_detail_content_revision_and_image_ownership_are_enforced(self):
        oid,pid,detail=self.product()
        with Session(self.engine) as db:
            image=ProductImage(product_id=pid,url='/uploads/products/test.png',position=0,role='gallery')
            db.add(image);db.commit();iid=image.id
        body={'expected_revision':1,'gallery':[{'id':iid,'color':'Blue','alt':'蓝色商品'}],
              'description':[{'type':'text','text':'说明文字 <script>作为纯文本</script>'},{'type':'image','image_id':iid}],
              'parameters':[{'name':'材质','value':'铝合金'}]}
        response=self.client.put(f'/api/products/{pid}/detail',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['description'][1]['url'],'/uploads/products/test.png')
        self.assertEqual(self.client.put(f'/api/products/{pid}/detail',json=body).status_code,409)
        body.update(expected_revision=2,description=[{'type':'image','image_id':iid+999}])
        self.assertEqual(self.client.put(f'/api/products/{pid}/detail',json=body).status_code,422)
        self.assertEqual(self.client.delete(f'/api/products/{pid}/images/{iid}?expected_revision=2').status_code,409)

    def test_default_sku_backfill_is_idempotent_and_never_changes_aggregate_balances(self):
        oid,pid,detail=self.product()
        before=self.stock(pid)
        with Session(self.engine) as db:
            initialize_product_skus(db)
            initialize_product_skus(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductSKU).where(ProductSKU.product_id==pid)),1)
        self.assertEqual(self.stock(pid),before)

    def test_dispatch_and_return_inspection_restore_only_the_original_variant(self):
        oid,pid,detail=self.product()
        variant=self.variant(pid)
        self.allocate(pid,detail['skus'][0]['id'],variant['id'])
        original=self.client.get(f'/api/orders/{oid}').json()
        response=self.client.post('/api/orders',json={'customer_id':original['customer_id'],'address_id':original['address_id'],
            'items':[{'product_id':pid,'sku_id':variant['id'],'quantity':2}]})
        self.assertEqual(response.status_code,201,response.text)
        order=response.json(); oid=order['id']; itemid=order['items'][0]['id']
        self.assertEqual(self.client.patch(f'/api/orders/{oid}/status',json={'status':'paid_pending_shipment'}).status_code,200)
        self.assertEqual(self.record(oid).status_code,200)
        self.assertEqual(self.dispatch(oid,key='sku-return-dispatch').status_code,200)
        case=self.client.post(f'/api/orders/{oid}/aftersales',json={'kind':'return_refund','requested_amount':'30.00','reason':'SKU验收',
            'items':[{'order_item_id':itemid,'quantity':2}],'idempotency_key':'sku-return-case-123'}).json()
        url=f"/api/orders/{oid}/aftersales/{case['id']}/inspection"
        payload={'expected_revision':1,'note':'一件完好一件损坏','items':[{'order_item_id':itemid,'accepted_quantity':1,'damaged_quantity':1}]}
        response=self.client.post(url,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.post(url,json=payload).status_code,200)
        result=self.client.get(f'/api/products/{pid}').json()['skus']
        self.assertEqual(result[0]['stock'],5)
        self.assertEqual(result[1]['stock'],2)
        self.assertEqual(result[1]['reserved'],0)

    def test_sku_stock_count_and_stale_draft_do_not_change_other_variants(self):
        oid,pid,detail=self.product()
        variant=self.variant(pid)
        self.allocate(pid,detail['skus'][0]['id'],variant['id'])
        body={'kind':'count','reason':'按规格实盘','items':[{'product_id':pid,'sku_id':variant['id'],'counted_on_hand':4}],
              'idempotency_key':'sku-count-draft-123'}
        response=self.client.post('/api/inventory/adjustments',json=body)
        self.assertEqual(response.status_code,201,response.text)
        aid=response.json()['id']
        response=self.client.post(f'/api/inventory/adjustments/{aid}/confirm')
        self.assertEqual(response.status_code,200,response.text)
        result=self.client.get(f'/api/products/{pid}').json()['skus']
        self.assertEqual([row['stock'] for row in result],[5,4])
        body['idempotency_key']='sku-count-stale-123'
        draft=self.client.post('/api/inventory/adjustments',json=body).json()
        self.allocate(pid,detail['skus'][0]['id'],variant['id'],quantity=1,key='after-count-allocation')
        self.assertEqual(self.client.post(f"/api/inventory/adjustments/{draft['id']}/confirm").status_code,409)

    def test_gallery_upload_error_cleanup_and_structured_content_validation(self):
        from app.features.products import storage
        from tempfile import TemporaryDirectory
        from pathlib import Path
        oid,pid,detail=self.product()
        png=b'\x89PNG\r\n\x1a\n'+b'isolated-test-image'
        with TemporaryDirectory() as folder, patch.object(storage,'PRODUCT_IMAGE_DIR',Path(folder)):
            response=self.client.post(f'/api/products/{pid}/images',data={'expected_revision':'1','role':'detail'},files={'image':('a.png',png,'image/png')})
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(len(list(Path(folder).iterdir())),1)
            response=self.client.post(f'/api/products/{pid}/images',data={'expected_revision':'1'},files={'image':('a.png',png,'image/png')})
            self.assertEqual(response.status_code,409)
            self.assertEqual(len(list(Path(folder).iterdir())),1)
        response=self.client.put(f'/api/products/{pid}/detail',json={'expected_revision':2,'description':[{'type':'html','text':'<script/>'}]})
        self.assertEqual(response.status_code,422)

    def test_schema_upgrade_adds_nullable_fields_and_can_run_twice(self):
        from sqlalchemy import create_engine, text
        from app.features.products.bootstrap.migration import initialize_product_schema
        engine=create_engine('sqlite://')
        try:
            with engine.begin() as db:
                db.execute(text('CREATE TABLE order_items (id INTEGER PRIMARY KEY, product_name VARCHAR(180))'))
                db.execute(text("INSERT INTO order_items VALUES(1,'historical')"))
            initialize_product_schema(engine)
            initialize_product_schema(engine)
            self.assertTrue({'sku_id','sku_code','sku_label'}.issubset({c['name'] for c in inspect(engine).get_columns('order_items')}))
            with engine.connect() as db:
                self.assertEqual(db.execute(text('SELECT product_name FROM order_items WHERE id=1')).scalar_one(),'historical')
        finally:
            engine.dispose()


import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from types import SimpleNamespace
import test_order_status as mysql_fixtures


@unittest.skipUnless(os.getenv('RUN_MYSQL_TESTS') == '1', 'Set RUN_MYSQL_TESTS=1 for isolated MySQL tests')
class ProductSKUMySQLTests(OrderFixtures, unittest.TestCase):
    setUpClass = classmethod(mysql_fixtures.MySQLConcurrencyTests.setUpClass.__func__)
    drop_test_database = classmethod(mysql_fixtures.MySQLConcurrencyTests.drop_test_database.__func__)

    def test_concurrent_buyers_cannot_oversell_the_last_variant(self):
        from app.features.products.details.service import save_sku, transfer_stock
        from app.features.products.details.schemas import SKUWrite, StockTransfer
        from app.features.orders.crud import OrderCRUD
        from app.features.orders.schemas import OrderItemCreate
        oid, pid, payload = self.seed_order()
        actor = SimpleNamespace(id=None, username='sku-concurrency-test')
        with Session(self.engine, expire_on_commit=False) as db:
            result=save_sku(db,pid,SKUWrite(code='LAST-'+uuid4().hex,color='Blue',size='M',price='15.00'))
            source,target=result['skus'][0]['id'],result['skus'][-1]['id']
            transfer_stock(db,pid,StockTransfer(from_sku_id=source,to_sku_id=target,quantity=1,reason='isolated fixture allocation',idempotency_key=str(uuid4())),actor)
        order_payload=payload.model_copy(update={'items':[OrderItemCreate(product_id=pid,sku_id=target,quantity=1)]})
        barrier=Barrier(2,timeout=10)
        def buy():
            with Session(self.engine,expire_on_commit=False,autoflush=False) as db:
                barrier.wait()
                order,error=OrderCRUD(db).create_order(order_payload,actor,str(uuid4()))
                if error:db.rollback()
                return error is None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(buy) for _ in range(2)]
            results=[future.result(timeout=20) for future in futures]
        self.assertEqual(sum(results),1)
        with Session(self.engine) as db:
            sku=db.get(ProductSKU,target)
            self.assertEqual((sku.stock,sku.reserved),(0,1))
            self.assertEqual(db.get(ProductModel,pid).stock,db.scalar(select(func.sum(ProductSKU.stock)).where(ProductSKU.product_id==pid)))
