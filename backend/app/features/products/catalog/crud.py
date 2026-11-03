"""商品 CRUD：封装 MySQL 查询、筛选、排序、分页和增删改操作。"""
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.features.operations.models import Reservation, StockAlert
from app.features.operations.inventory import opening, evaluate_alert

from app.features.products.models import ProductContent, ProductImage, ProductModel, ProductSKU
from app.features.products.favorites.models import ProductFavorite


class ProductCRUD:
    """商品数据库操作集合；HTTP 参数和响应格式由 router/schemas 层处理。"""

    def __init__(self, db: Session) -> None:
        """绑定当前请求的数据库 Session，所有方法共享该事务上下文。"""
        self.db = db

    def list_products(
        self,
        *,
        q: str,
        categories: list[str],
        colors: list[str],
        shipping_options: list[str],
        min_price: float | None,
        max_price: float | None,
        min_rating: float | None,
        status: str | None,
        sort: str,
        page: int,
        page_size: int,
    ) -> tuple[list[ProductModel], int]:
        """在数据库侧执行筛选、排序和分页，同时返回符合条件的总数。"""
        # [商品列表页 / 数据查询] 所有筛选与排序在数据库执行，再按页返回商品行和总数。
        statement = select(ProductModel)
        if q.strip():
            search = f"%{q.strip()}%"
            statement = statement.where(or_(ProductModel.name.ilike(search), ProductModel.category.ilike(search)))
        if categories:
            statement = statement.where(ProductModel.category.in_(categories))
        if colors:
            statement = statement.where(or_(ProductModel.color.in_(colors), ProductModel.id.in_(
                select(ProductSKU.product_id).where(ProductSKU.color.in_(colors), ProductSKU.active.is_(True)))))
        if shipping_options:
            statement = statement.where(ProductModel.shipping.in_(shipping_options))
        if min_price is not None:
            statement = statement.where(ProductModel.price >= min_price)
        if max_price is not None:
            statement = statement.where(ProductModel.price <= max_price)
        if min_rating is not None:
            statement = statement.where(ProductModel.rating >= min_rating)
        if status:
            # 上架查询包含旧库存式状态，避免升级后历史在售商品突然消失。
            from app.features.products.constants import SELLABLE_STATUSES
            statement = statement.where(ProductModel.status.in_(SELLABLE_STATUSES) if status == "Active" else ProductModel.status == status)

        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        sort_columns = {
            "rating": ProductModel.rating.desc(),
            "price_asc": ProductModel.price.asc(),
            "price_desc": ProductModel.price.desc(),
            "stock": ProductModel.stock.asc(),
        }
        # 同价、同库存商品用 ID 保证顺序稳定，翻页时不因排序并列而重漏记录。
        statement = statement.order_by(sort_columns.get(sort, ProductModel.id.asc()), ProductModel.id.asc())
        products = self.db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
        from app.features.products.catalog.state import attach_inventory_state
        return attach_inventory_state(self.db, list(products)), total

    def get_product_filter_options(self) -> dict[str, list[str]]:
        """读取商品表中的唯一筛选值，供分类、颜色和配送方式控件使用。"""
        # [商品列表页 / 筛选面板] 从商品表提取去重后的分类、颜色和配送方式。
        return {
            "categories": list(self.db.scalars(select(ProductModel.category).distinct().order_by(ProductModel.category))),
            "colors": sorted(set(self.db.scalars(select(ProductModel.color))) | set(self.db.scalars(select(ProductSKU.color).where(ProductSKU.active.is_(True))))),
            "shipping_options": list(self.db.scalars(select(ProductModel.shipping).distinct().order_by(ProductModel.shipping))),
        }

    def get_product(self, product_id: int) -> ProductModel | None:
        """按主键读取单个商品；不存在时返回 None。"""
        return self.db.get(ProductModel, product_id)

    def create_product(self, product_data: dict[str, Any], actor=None) -> ProductModel:
        """插入新商品并提交事务，提交后刷新数据库生成的字段。"""
        row = ProductModel(**product_data)
        self.db.add(row)
        self.db.flush()
        from app.features.products.sku_inventory import ensure_default_sku
        ensure_default_sku(self.db, row)
        self.db.add(ProductImage(product_id=row.id, url=row.image_url, alt=row.name, color=row.color, position=0, role='gallery'))
        opening(self.db, row, actor)
        evaluate_alert(self.db, row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_product(self, product_id: int, product_data: dict[str, Any]) -> ProductModel | None:
        """按主键更新给定字段；调用方提供完整的可写商品字段。"""
        row = self.db.scalar(select(ProductModel).where(ProductModel.id == product_id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            return None
        if product_data.get('stock', row.stock) != row.stock:
            raise HTTPException(409, '可售库存不能直接修改，请通过库存中心录入补货单')
        from app.features.products.sku_inventory import ensure_default_sku
        skus = ensure_default_sku(self.db, row)
        if len(skus) == 1:
            skus[0].price = product_data.get('price', row.price)
            skus[0].color = product_data.get('color', row.color)
            skus[0].revision += 1
        elif product_data.get('price', row.price) != row.price:
            raise HTTPException(409, '多规格商品请在 SKU 管理中修改各规格价格')
        if product_data.get('image_url', row.image_url) != row.image_url:
            images = list(self.db.scalars(select(ProductImage).where(ProductImage.product_id == product_id)))
            cover = next((image for image in images if image.url == product_data['image_url']), None)
            if cover is None:
                if len([image for image in images if image.role == 'gallery']) >= 20:
                    raise HTTPException(409, '轮播已满，请先在商品详情中整理图片')
                cover = ProductImage(product_id=product_id, url=product_data['image_url'], alt=row.name, color='', role='gallery', position=0)
                self.db.add(cover)
            for image in images:
                image.position += 1
            cover.position, cover.role = 0, 'gallery'
            content = self.db.scalar(select(ProductContent).where(ProductContent.product_id == product_id)
                                     .with_for_update().execution_options(populate_existing=True))
            if content is None:
                content = ProductContent(product_id=product_id, revision=1, description=[], parameters=[])
                self.db.add(content)
            content.revision += 1
        for field, value in product_data.items():
            setattr(row, field, value)
        self.db.flush()
        evaluate_alert(self.db, row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete_product(self, product_id: int) -> bool:
        """删除商品并提交事务；返回值表示该主键是否存在。"""
        row = self.db.scalar(select(ProductModel).where(ProductModel.id == product_id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            return False
        if self.db.scalar(select(Reservation.id).where(Reservation.product_id == product_id, Reservation.status == 'active').limit(1).with_for_update()):
            raise HTTPException(409, '商品仍有订单预留，不能删除；请先处理关联订单')
        alert = self.db.scalar(select(StockAlert).where(StockAlert.active_product_id == product_id))
        if alert:
            from app.features.operations.models import utcnow
            alert.active_product_id = None
            alert.alert_status = 'closed'
            alert.close_reason = 'product_deleted'
            alert.resolved_at = utcnow()
            alert.last_evaluated_at = alert.resolved_at
        for model in (ProductFavorite, ProductImage, ProductContent, ProductSKU):
            self.db.execute(delete(model).where(model.product_id == row.id))
        self.db.delete(row)
        self.db.commit()
        return True
