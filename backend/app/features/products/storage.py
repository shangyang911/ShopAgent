"""商品图片本地存储：只接收常见栅格图片并返回可公开访问的相对路径。"""
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

PRODUCT_IMAGE_DIR = Path(__file__).resolve().parents[3] / "uploads" / "products"
MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024


class InvalidProductImage(ValueError):
    """上传文件不是支持的图片类型或 MIME 与实际文件内容不一致。"""

    pass


class ProductImageTooLarge(ValueError):
    """上传图片超过应用允许的最大字节数。"""

    pass


async def save_product_image(image: UploadFile) -> str:
    """限制读取大小、核验文件签名并保存图片，返回 API 可访问的相对 URL。"""
    content = await image.read(MAX_PRODUCT_IMAGE_BYTES + 1)
    if len(content) > MAX_PRODUCT_IMAGE_BYTES:
        raise ProductImageTooLarge("Product images must be 5 MB or smaller.")

    signatures = (
        ("image/jpeg", ".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
        ("image/png", ".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
        ("image/webp", ".webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
    )
    for mime_type, extension, matches in signatures:
        if matches(content):
            if image.content_type not in (None, mime_type):
                raise InvalidProductImage("The uploaded image type does not match its file content.")
            PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid4().hex}{extension}"
            (PRODUCT_IMAGE_DIR / filename).write_bytes(content)
            return f"/uploads/products/{filename}"

    raise InvalidProductImage("Choose a JPEG, PNG, or WebP image.")


def delete_product_image(image_url: str) -> None:
    """仅删除本应用上传目录中的图片，拒绝其他 URL 和目录穿越路径。"""
    prefix = "/uploads/products/"
    if not image_url.startswith(prefix):
        return

    filename = image_url.removeprefix(prefix)
    if not filename or Path(filename).name != filename:
        return

    image_path = PRODUCT_IMAGE_DIR / filename
    if image_path.resolve().parent != PRODUCT_IMAGE_DIR.resolve():
        return
    try:
        image_path.unlink(missing_ok=True)
    except OSError:
        # A stale or locked file must not block a successful product update/delete.
        pass


def delete_unused_product_image(db, image_url):
    from sqlalchemy import select
    from .models import ProductImage, ProductModel
    if db.scalar(select(ProductModel.id).where(ProductModel.image_url == image_url).limit(1)) is not None:
        return
    if db.scalar(select(ProductImage.id).where(ProductImage.url == image_url).limit(1)) is not None:
        return
    delete_product_image(image_url)


async def store_uploaded_image(image: UploadFile) -> str:
    """把图片存储层异常转换为 HTTP 413/415，统一商品新增和更新的错误响应。"""
    try:
        return await save_product_image(image)
    except ProductImageTooLarge as error:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(error)) from error
    except InvalidProductImage as error:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)) from error
