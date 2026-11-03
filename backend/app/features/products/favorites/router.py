"""登录账号的收藏查询、收藏与取消；重复提交保持幂等。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.products.catalog.schemas import Product
from app.features.products.catalog.state import attach_inventory_state
from app.features.products.models import ProductModel
from .models import ProductFavorite

router = APIRouter(prefix="/products/favorites", tags=["product-favorites"])


@router.get("", response_model=list[Product])
def list_favorites(db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    rows = db.scalars(select(ProductModel).join(ProductFavorite, ProductFavorite.product_id == ProductModel.id)
                      .where(ProductFavorite.user_id == actor.id)
                      .order_by(ProductFavorite.created_at.desc(), ProductModel.id.desc())).all()
    return attach_inventory_state(db, list(rows))


@router.put("/{product_id}", status_code=204)
def add_favorite(product_id: int, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    # 与商品删除共用商品锁，也串行化同一商品的重复收藏提交。
    product = db.scalar(select(ProductModel).where(ProductModel.id == product_id).with_for_update())
    if product is None:
        raise HTTPException(404, "商品不存在")
    if db.get(ProductFavorite, (actor.id, product_id)) is None:
        db.add(ProductFavorite(user_id=actor.id, product_id=product_id))
    db.commit()
    return Response(status_code=204)


@router.delete("/{product_id}", status_code=204)
def remove_favorite(product_id: int, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    db.execute(delete(ProductFavorite).where(ProductFavorite.user_id == actor.id,
                                            ProductFavorite.product_id == product_id))
    db.commit()
    return Response(status_code=204)
