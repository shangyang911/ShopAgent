"""商品功能 HTTP 路由：解析请求、调用 CRUD 并返回 API 响应。"""
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.auth.models import UserModel
from app.features.products.catalog.crud import ProductCRUD
from app.features.products.catalog.schemas import Product, ProductCreate, ProductListResponse, ProductOptions, ProductUpdate
from app.features.products.storage import (
    delete_product_image,
    store_uploaded_image,
    delete_unused_product_image,
)

# [商品管理功能 / 路由鉴权] 商品列表和全部写操作都只允许已登录管理员调用。
router = APIRouter(prefix="/products", tags=["products"], dependencies=[Depends(require_current_user)])


@router.get("", response_model=ProductListResponse)
def list_products(
    q: str = "",
    category: Annotated[list[str] | None, Query()] = None,
    color: Annotated[list[str] | None, Query()] = None,
    shipping: Annotated[list[str] | None, Query()] = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    status: str | None = None,
    sort: str = "featured",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=48),
    db: Session = Depends(get_db),
) -> ProductListResponse:
    # [商品列表页 / 查询] 路由解析 HTTP 参数，CRUD 执行数据库筛选和分页。
    rows, total = ProductCRUD(db).list_products(
        q=q,
        categories=category or [],
        colors=color or [],
        shipping_options=shipping or [],
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        status=status,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return ProductListResponse(items=rows, total=total, page=page, page_size=page_size)


@router.get("/options", response_model=ProductOptions)
def product_options(db: Session = Depends(get_db)) -> ProductOptions:
    # [商品列表页 / 筛选面板] 从 CRUD 读取筛选选项并构造成 API 响应。
    options = ProductCRUD(db).get_product_filter_options()
    return ProductOptions(**options)


@router.post("", response_model=Product, status_code=status.HTTP_201_CREATED)
async def create_product(
    product: Annotated[ProductCreate, Form()],
    image: Annotated[UploadFile, File()],
    db: Session = Depends(get_db),
    actor: UserModel = Depends(require_current_user),
) -> Product:
    """创建商品记录；数据库写入失败时回滚刚刚保存的图片文件。"""
    image_url = await store_uploaded_image(image)
    try:
        # 没有评价系统时不接受人工制造评分；数据库默认保留零值。
        row = ProductCRUD(db).create_product({**product.model_dump(exclude={"rating", "reviews"}), "image_url": image_url}, actor)
    except Exception:
        delete_product_image(image_url)
        raise
    from app.features.products.catalog.state import attach_inventory_state
    return Product.model_validate(attach_inventory_state(db, [row])[0])


@router.put("/{product_id}", response_model=Product)
async def update_product(
    product_id: int,
    product: Annotated[ProductUpdate, Form()],
    image: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
) -> Product:
    """更新商品字段和可选图片；提交成功后才删除旧图片。"""
    crud = ProductCRUD(db)
    existing = crud.get_product(product_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    old_image_url = existing.image_url
    new_image_url = await store_uploaded_image(image) if image is not None else None
    product_data = product.model_dump(exclude={"rating", "reviews"})
    if new_image_url is not None:
        product_data["image_url"] = new_image_url
    try:
        row = crud.update_product(product_id, product_data)
    except Exception:
        if new_image_url is not None:
            delete_product_image(new_image_url)
        raise
    if row is None:
        if new_image_url is not None:
            delete_product_image(new_image_url)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if new_image_url is not None:
        delete_unused_product_image(db, old_image_url)
    from app.features.products.catalog.state import attach_inventory_state
    return Product.model_validate(attach_inventory_state(db, [row])[0])


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: Session = Depends(get_db)) -> Response:
    """删除商品及其本地图片；商品不存在时返回 404。"""
    crud = ProductCRUD(db)
    product = crud.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    from app.features.products.models import ProductImage
    from sqlalchemy import select
    image_urls = {product.image_url, *db.scalars(select(ProductImage.url).where(ProductImage.product_id == product_id))}
    crud.delete_product(product_id)
    for image_url in image_urls:
        delete_unused_product_image(db, image_url)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
