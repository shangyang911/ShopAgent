"""数据库基础设施：提供请求级 Session，并在服务启动时创建表和初始化数据。"""
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DATABASE_URL
from app.db.base import Base
from app.features.auth.models import AuthSessionModel, UserModel  # noqa: F401 - register auth tables
from app.features.customers.models import CustomerAddressModel, CustomerModel  # noqa: F401 - register customer tables
from app.features.orders.models import OrderItemModel, OrderModel  # noqa: F401 - register order tables
from app.features.products.models import ProductModel  # noqa: F401 - register model metadata
from app.features.products.favorites.models import ProductFavorite  # noqa: F401 - register favorites table
from app.features.notifications.models import NotificationRead  # noqa: F401 - register read receipts
from app.features.products.batch.models import ProductBatchLog  # noqa: F401 - batch audit receipts
from app.features.customers.details.models import CustomerProfile  # noqa: F401 - customer follow-up metadata
from app.features.products.bootstrap.seed import seed_products_if_needed
from app.features.operations import models as operation_models  # noqa: F401
from app.features.agent import models as agent_models  # noqa: F401
from app.features.agent import telemetry as agent_telemetry  # noqa: F401
from app.features.knowledge import models as knowledge_models  # noqa: F401
from app.features.shipping.parcels import models as parcel_models  # noqa: F401
from app.features.shipping.labels import models as label_models  # noqa: F401
from app.features.aftersales import models as aftersale_models  # noqa: F401 - additive aftersale tables
from app.features.inventory import models as adjustment_models  # noqa: F401 - stock adjustment tables
from app.features.shipping.tracking import parcel_models as parcel_tracking_models  # noqa: F401

# 上面的模型导入即使没有直接被调用也必须保留：它们负责向 Base.metadata 注册数据表。

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """为每个 HTTP 请求提供独立 Session，并确保请求结束后关闭连接。"""
    # 业务方法负责 commit；异常请求最终关闭 Session 时，未提交的事务会回滚。
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    """创建表，兼容旧订单状态，再执行幂等的商品种子和运营期初初始化。"""
    from app.features.products.bootstrap.migration import initialize_product_schema
    initialize_product_schema(engine)
    with SessionLocal() as db:
        # [订单状态迁移] 兼容早期开发版本中的旧状态字符串。
        db.execute(text("""
            UPDATE orders
            SET status = CASE status
                WHEN 'confirmed' THEN 'paid_pending_shipment'
                WHEN 'processing' THEN 'paid_pending_shipment'
                WHEN 'pending_delivery' THEN 'paid_pending_shipment'
                WHEN 'delivered' THEN 'completed'
                ELSE status
            END
            WHERE status IN ('confirmed', 'processing', 'pending_delivery', 'delivered')
        """))
        db.commit()
        seed_products_if_needed(db)
        from app.features.operations.migration import initialize_operations
        initialize_operations(db)
        from app.features.products.sku_inventory import initialize_product_skus
        initialize_product_skus(db)
