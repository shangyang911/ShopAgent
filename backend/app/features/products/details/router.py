"""Authenticated product-detail endpoints, separate from legacy catalogue forms."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.products.details import service
from app.features.products.details.schemas import DetailWrite, ProductDetail, SKUUpdate, SKUWrite, StockTransfer
from app.features.products.models import ProductImage
from app.features.products.storage import store_uploaded_image
from app.features.products.storage import delete_product_image, delete_unused_product_image

router = APIRouter(prefix='/products', tags=['product-details'], dependencies=[Depends(require_current_user)])


@router.get('/{product_id}', response_model=ProductDetail)
def get_detail(product_id: int, db: Session = Depends(get_db)):
    return service.detail(db, product_id)


@router.put('/{product_id}/detail', response_model=ProductDetail)
def put_detail(product_id: int, payload: DetailWrite, db: Session = Depends(get_db)):
    return service.save_detail(db, product_id, payload)


@router.post('/{product_id}/images', status_code=201, response_model=ProductDetail)
async def upload_image(product_id: int, image: Annotated[UploadFile, File()],
                       expected_revision: Annotated[int, Form(ge=1)],
                       role: Annotated[Literal['gallery', 'detail'], Form()] = 'gallery',
                       alt: Annotated[str, Form(max_length=180)] = '',
                       color: Annotated[str, Form(max_length=40)] = '', db: Session = Depends(get_db)):
    # Upload first, then acquire the product lock; slow clients must not hold stock locks.
    url = await store_uploaded_image(image)
    try:
        product = service.lock_product(db, product_id)
        content = service.content_for_write(db, product, expected_revision)
        rows = list(db.scalars(select(ProductImage).where(ProductImage.product_id == product_id)))
        if len(rows) >= 100 or (role == 'gallery' and sum(row.role == 'gallery' for row in rows) >= 20):
            raise HTTPException(422, '每个商品最多 100 张图片，其中轮播最多 20 张')
        db.add(ProductImage(product_id=product_id, url=url, alt=alt, color=color.strip(),
                            role=role, position=len(rows)))
        content.revision += 1
        db.commit()
    except Exception:
        db.rollback()
        delete_product_image(url)
        raise
    return service.detail(db, product_id)


@router.delete('/{product_id}/images/{image_id}', response_model=ProductDetail)
def remove_image(product_id: int, image_id: int, expected_revision: int = Query(ge=1), db: Session = Depends(get_db)):
    product = service.lock_product(db, product_id)
    content = service.content_for_write(db, product, expected_revision)
    image = db.get(ProductImage, image_id)
    if image is None or image.product_id != product_id:
        raise HTTPException(404, '图片不存在')
    if any(block.get('image_id') == image_id for block in content.description):
        raise HTTPException(409, '图片仍被图文详情引用，请先修改详情')
    url = image.url
    db.delete(image)
    db.flush()
    if product.image_url == url:
        replacement = db.scalar(select(ProductImage).where(ProductImage.product_id == product_id, ProductImage.role == 'gallery')
                                .order_by(ProductImage.position, ProductImage.id))
        if replacement is None:
            raise HTTPException(409, '至少保留一张商品主图')
        product.image_url = replacement.url
    content.revision += 1
    db.commit()
    delete_unused_product_image(db, url)
    return service.detail(db, product_id)


@router.post('/{product_id}/skus', status_code=201, response_model=ProductDetail)
def create_sku(product_id: int, payload: SKUWrite, db: Session = Depends(get_db)):
    return service.save_sku(db, product_id, payload)


@router.put('/{product_id}/skus/{sku_id}', response_model=ProductDetail)
def update_sku(product_id: int, sku_id: int, payload: SKUUpdate, db: Session = Depends(get_db)):
    return service.save_sku(db, product_id, payload, sku_id)


@router.post('/{product_id}/sku-stock-transfer', response_model=ProductDetail)
def allocate_stock(product_id: int, payload: StockTransfer, db: Session = Depends(get_db), actor: UserModel = Depends(require_current_user)):
    return service.transfer_stock(db, product_id, payload, actor)
