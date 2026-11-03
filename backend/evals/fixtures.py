# [评测数据隔离] 每条任务使用独立内存数据库和合成身份，不连接业务数据库。
"""Every task gets an independent in-memory SQLite database and synthetic credentials."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
from app.features.agent.models import AgentAction
from app.features.agent.service import AgentStore
from app.features.auth.models import UserModel, AuthSessionModel
from app.features.auth.security import hash_session_token
from app.features.customers.models import CustomerModel, CustomerAddressModel
from app.features.orders.crud import OrderCRUD
from app.features.orders.models import OrderModel, OrderStatus
from app.features.orders.schemas import OrderCreate
from app.features.products.models import ProductModel, ProductSKU
from app.features.operations.models import utcnow
from app.features.shipping.labels.models import LabelTask  # register tables before create_all
from app.features.shipping.parcels.models import ExtraParcel
from app.features.shipping.tracking.models import ShipmentTrackingModel, TrackingEventModel


class Fixture:
    token = 'synthetic-evaluation-token'

    def __init__(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.store = AgentStore(self.factory)
        with self.factory() as db:
            actor = UserModel(id=1, username='eval-admin', password_hash='not-a-real-password')
            customer = CustomerModel(name='评测客户', phone='13800000000')
            db.add_all([actor, customer])
            db.flush()
            db.add(AuthSessionModel(user_id=1, token_hash=hash_session_token(self.token), expires_at=utcnow()+timedelta(days=1)))
            address = CustomerAddressModel(customer_id=customer.id, recipient_name='评测客户', phone='13800000000',
                                           country='CN', province='测试省', city='测试市', district='测试区', detail='合成地址')
            product = ProductModel(name='评测键盘', category='Test', price=Decimal('10.00'), stock=10,
                                   status='Active', image_url='/eval.png', color='黑色', rating=4, reviews=0, shipping='Standard')
            db.add_all([address, product])
            db.commit()
            for i in range(2):
                order, error = OrderCRUD(db).create_order(OrderCreate(customer_id=customer.id, address_id=address.id,
                    items=[{'product_id': product.id, 'quantity': 2}]), actor)
                if error:
                    raise RuntimeError(error)
                order.order_number = f'EVAL-{i+1:03}'
                db.commit()
                if i == 1:
                    OrderCRUD(db).update_status(order.id, OrderStatus.PAID_PENDING_SHIPMENT, actor)
        self.conversation = self.store.create_conversation(1)['id']

    def snapshot(self):
        with self.factory() as db:
            return {'orders': [(r.id, r.status.value) for r in db.scalars(select(OrderModel).order_by(OrderModel.id))],
                    'stock': [(r.id, r.stock) for r in db.scalars(select(ProductModel).order_by(ProductModel.id))],
                    'skus': [(r.id, r.stock, r.reserved) for r in db.scalars(select(ProductSKU).order_by(ProductSKU.id))]}

    def actions(self):
        with self.factory() as db:
            return [r.state for r in db.scalars(select(AgentAction))]

    def close(self):
        self.engine.dispose()
