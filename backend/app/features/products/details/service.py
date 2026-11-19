"""维护商品详情、图片与 SKU；库存分配通过审计流水完成，不直接改总库存。"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.features.operations.common import wire
from app.features.products.models import ProductContent, ProductImage, ProductModel, ProductSKU
from app.features.products.sku_inventory import ensure_default_sku, resolve_sku, sku_label


def lock_product(db, product_id):
    """先锁商品再处理 SKU，刷新会话中可能由页面查询留下的旧对象。"""
    product = db.scalar(select(ProductModel).where(ProductModel.id == product_id)
                        .with_for_update().execution_options(populate_existing=True))
    if product is None:
        raise HTTPException(404, '商品不存在')
    return product


def content_for_write(db, product, revision):
    """商品锁串行化写入，版本号防止旧页面覆盖其他用户已保存的详情。"""
    content = db.scalar(select(ProductContent).where(ProductContent.product_id == product.id)
                        .with_for_update().execution_options(populate_existing=True))
    if content is None:
        content = ProductContent(product_id=product.id, revision=1, description=[], parameters=[])
        db.add(content)
        db.flush()
    if content.revision != revision:
        raise HTTPException(409, '商品详情已更新，请刷新后再编辑')
    return content


def detail(db, product_id):
    from app.features.products.catalog.schemas import Product
    from app.features.products.catalog.state import attach_inventory_state
    product = db.get(ProductModel, product_id)
    if product is None:
        raise HTTPException(404, '商品不存在')
    content = db.get(ProductContent, product_id)
    images = list(db.scalars(select(ProductImage).where(ProductImage.product_id == product_id)
                            .order_by(ProductImage.position, ProductImage.id)))
    skus = list(db.scalars(select(ProductSKU).where(ProductSKU.product_id == product_id).order_by(ProductSKU.id)))
    gallery = [wire(image) for image in images if image.role == 'gallery']
    if not gallery and product.image_url:
        gallery = [dict(id=None, url=product.image_url, alt=product.name, color='', position=0, role='gallery')]
    image_map = {image.id: image.url for image in images}
    blocks = [{**block, **({'url': image_map.get(block.get('image_id'))} if block['type'] == 'image' else {})}
              for block in (content.description if content else [])]
    return {**Product.model_validate(attach_inventory_state(db, [product])[0]).model_dump(mode='json'),
            'revision': content.revision if content else 1, 'images': gallery, 'media': wire(images),
            'description': blocks, 'parameters': content.parameters if content else [],
            'skus': [{**wire(sku), 'label': sku_label(sku), 'available_quantity': sku.stock,
                      'reserved_quantity': sku.reserved, 'on_hand_quantity': sku.stock + sku.reserved} for sku in skus],
            'options': {'colors': list(dict.fromkeys(sku.color for sku in skus if sku.active and sku.color)),
                        'sizes': list(dict.fromkeys(sku.size for sku in skus if sku.active and sku.size))}}


def save_detail(db, product_id, payload):
    product = lock_product(db, product_id)
    content = content_for_write(db, product, payload.expected_revision)
    images = {image.id: image for image in db.scalars(select(ProductImage).where(ProductImage.product_id == product_id))}
    needed = {row.id for row in payload.gallery} | {row.image_id for row in payload.description if row.type == 'image'}
    if not needed.issubset(images):
        raise HTTPException(422, '详情或轮播引用了不属于该商品的图片')
    for image in images.values():
        image.role = 'detail'
    for position, value in enumerate(payload.gallery):
        image = images[value.id]
        image.position, image.alt, image.color, image.role = position, value.alt, value.color.strip(), 'gallery'
    if payload.gallery:
        product.image_url = images[payload.gallery[0].id].url
    content.description = [row.model_dump() for row in payload.description]
    content.parameters = [row.model_dump() for row in payload.parameters]
    content.revision += 1
    db.commit()
    return detail(db, product_id)


def update_catalog_price(db, product):
    """同步在售规格的最低展示价；订单成交价由所选 SKU 另行冻结。"""
    db.flush()
    # MySQL 可重复读下，普通 MIN 查询仍可能使用鉴权时建立的旧快照。
    # 持有商品锁时以当前读取得规格价格，再汇总，避免并发调价后写回旧最低价。
    prices = db.scalars(select(ProductSKU.price).where(
        ProductSKU.product_id == product.id, ProductSKU.active.is_(True)).order_by(ProductSKU.id).with_for_update())
    price = min(prices, default=None)
    if price is not None:
        product.price = price


def save_sku(db, product_id, payload, sku_id=None):
    product = lock_product(db, product_id)
    ensure_default_sku(db, product)
    if sku_id is None:
        row = ProductSKU(product_id=product_id, stock=0, reserved=0, is_default=False, revision=1)
        db.add(row)
    else:
        row = resolve_sku(db, product, sku_id)
        if row.revision != payload.expected_revision:
            raise HTTPException(409, 'SKU 已更新，请刷新后编辑')
        if not payload.active and (row.stock or row.reserved):
            raise HTTPException(409, 'SKU 仍有库存或订单预留，请先处理库存')
        row.revision += 1
    for key, value in payload.model_dump(exclude={'expected_revision'}).items():
        setattr(row, key, value)
    try:
        update_catalog_price(db, product)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, 'SKU 编码或颜色/尺寸组合重复') from error
    return detail(db, product_id)


def transfer_stock(db, product_id, payload, actor):
    """同一事务记录转出与转入，只移动可售库存，商品总量和订单预留保持不变。"""
    from app.features.operations.inventory import evaluate_alert, move
    from app.features.operations.models import Movement
    product = lock_product(db, product_id)
    key = f'sku-allocation:{payload.idempotency_key}'
    previous = db.scalar(select(Movement).where(Movement.idempotency_key == key + ':out'))
    if previous:
        incoming = db.scalar(select(Movement).where(Movement.idempotency_key == key + ':in'))
        if (previous.product_id != product_id or previous.sku_id != payload.from_sku_id
            or previous.available_delta != -payload.quantity or previous.reason != payload.reason
            or incoming is None or incoming.sku_id != payload.to_sku_id):
            raise HTTPException(409, '库存分配提交键已用于其他内容')
        return detail(db, product_id)
    source = resolve_sku(db, product, payload.from_sku_id)
    target = resolve_sku(db, product, payload.to_sku_id, sellable=True)
    if source.stock < payload.quantity:
        raise HTTPException(409, '转出 SKU 可售库存不足，不能挪用已预留库存')
    for sku, delta, direction in ((source, -payload.quantity, 'out'), (target, payload.quantity, 'in')):
        move(db, product, kind='sku_allocate', available=delta, reserved=0,
             reference_type='product', reference_id=product.id, reference_number=str(product.id),
             key=key + ':' + direction, actor=actor, reason=payload.reason, sku_id=sku.id, check_alerts=False)
    evaluate_alert(db, product)
    db.commit()
    return detail(db, product_id)
