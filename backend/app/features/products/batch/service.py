"""商品锁、预览版本与幂等回执保护整批事务；任一冲突整批回滚。"""
from fastapi import HTTPException
from sqlalchemy import select
from app.features.auth.models import UserModel
from app.features.operations.common import digest
from app.features.operations.inventory import evaluate_alert
from app.features.products.models import ProductModel
from app.features.products.favorites.models import ProductFavorite
from .models import ProductBatchLog

STATUS_ACTIONS = {"activate": "Active", "deactivate": "Inactive", "archive": "Archived"}


def snapshot(db, actor, payload, lock=False):
    ids = sorted(set(payload.ids))
    statement = select(ProductModel).where(ProductModel.id.in_(ids)).order_by(ProductModel.id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    products = list(db.scalars(statement))
    if len(products) != len(ids):
        raise HTTPException(409, "部分商品已删除，请刷新列表后重试")
    favorite_query = select(ProductFavorite).where(ProductFavorite.user_id == actor.id, ProductFavorite.product_id.in_(ids))
    if lock:
        favorite_query = favorite_query.with_for_update().execution_options(populate_existing=True)
    favorites = {row.product_id: row for row in db.scalars(favorite_query)}
    changes = []
    for product in products:
        before = product.status if payload.action in STATUS_ACTIONS else ("已收藏" if product.id in favorites else "未收藏")
        after = STATUS_ACTIONS.get(payload.action, "已收藏" if payload.action == "favorite" else "未收藏")
        changes.append({"id": product.id, "name": product.name, "before": before, "after": after})
    token = digest({"user": actor.id, "action": payload.action, "changes": changes})
    return products, favorites, {"items": changes, "snapshot": token}


def apply(db, actor, payload):
    key = f"{actor.id}:{payload.idempotency_key}"
    payload_hash = digest({**payload.model_dump(), "ids": sorted(set(payload.ids))})
    # 同一账号的提交键先串行化；即使重试时商品已删除，也能返回原回执。
    db.scalar(select(UserModel).where(UserModel.id == actor.id).with_for_update())
    previous = db.scalar(select(ProductBatchLog).where(ProductBatchLog.key == key).with_for_update())
    if previous:
        if previous.payload_hash != payload_hash:
            raise HTTPException(409, "提交键已用于其他批量操作")
        return {"items": previous.changes, "count": len(previous.changes)}
    products, favorites, preview = snapshot(db, actor, payload, lock=True)
    if preview["snapshot"] != payload.snapshot:
        raise HTTPException(409, "商品状态或收藏已变化，请重新预览")
    for product in products:
        if payload.action in STATUS_ACTIONS:
            product.status = STATUS_ACTIONS[payload.action]
            evaluate_alert(db, product)
        elif payload.action == "favorite" and product.id not in favorites:
            db.add(ProductFavorite(user_id=actor.id, product_id=product.id))
        elif payload.action == "unfavorite" and product.id in favorites:
            db.delete(favorites[product.id])
    db.add(ProductBatchLog(key=key, user_id=actor.id, actor_name=actor.username,
                           payload_hash=payload_hash, changes=preview["items"]))
    db.commit()
    return {"items": preview["items"], "count": len(products)}
