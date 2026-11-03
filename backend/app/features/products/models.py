"""商品功能 ORM 模型：定义 products 表字段与查询索引。"""
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductModel(Base):
    """商品目录表；常用筛选字段带索引以加速列表查询。"""

    __tablename__ = "products"
    # 分类/状态与价格/评分的复合索引，分别支撑常见筛选组合。
    __table_args__ = (
        Index("ix_products_category_status", "category", "status"),
        Index("ix_products_price_rating", "price", "rating"),
    )

    # 商品主键和展示字段。
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Draft")
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # 商品属性及目录筛选使用的枚举式文本值。
    color: Mapped[str] = mapped_column(String(40), nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(2, 1), nullable=False, default=0)
    reviews: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipping: Mapped[str] = mapped_column(String(40), nullable=False)


class ProductContent(Base):
    __tablename__ = 'product_content'
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[list] = mapped_column(JSON, default=list)
    parameters: Mapped[list] = mapped_column(JSON, default=list)


class ProductImage(Base):
    __tablename__ = 'product_images'
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), index=True)
    url: Mapped[str] = mapped_column(String(500))
    alt: Mapped[str] = mapped_column(String(180), default='')
    color: Mapped[str] = mapped_column(String(40), default='')
    position: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16), default='gallery')


class ProductSKU(Base):
    __tablename__ = 'product_skus'
    __table_args__ = (UniqueConstraint('product_id', 'color', 'size', name='uq_product_sku_options'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id', ondelete='CASCADE'), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    color: Mapped[str] = mapped_column(String(40), default='')
    size: Mapped[str] = mapped_column(String(40), default='')
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
