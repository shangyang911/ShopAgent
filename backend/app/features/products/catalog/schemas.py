"""商品功能 API 契约：定义商品卡片、分页列表和筛选面板的数据结构。"""
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from app.features.products.catalog.state import PRODUCT_STATES, business_status


class ProductFields(BaseModel):
    """创建和更新商品共享的字段及输入约束。"""

    name: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=80)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    stock: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=32)
    color: str = Field(default="", max_length=40)
    # 历史评价字段保留读取兼容；HTTP 写入不会用它们覆盖已有评价。
    rating: Decimal = Field(default=0, ge=0, le=5, max_digits=2, decimal_places=1)
    reviews: int = Field(default=0, ge=0)
    shipping: str = Field(default="", max_length=40)

    @field_validator("name", "category", "status")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """去除用户输入首尾空格，并拒绝只包含空白的文本。"""
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("status")
    @classmethod
    def valid_status(cls, value):
        value = business_status(value)
        if value not in PRODUCT_STATES:
            raise ValueError("商品状态只能为草稿、上架、下架或归档")
        return value

    @field_validator("color", "shipping")
    @classmethod
    def trim_optional(cls, value):
        return value.strip()


class ProductCreate(ProductFields):
    """商品创建请求；图片由 multipart 文件字段单独传入。"""
    pass


class ProductUpdate(ProductFields):
    """商品更新请求；图片可选，未上传时保留已有图片。"""
    pass


class Product(BaseModel):
    """商品响应结构，可直接从 SQLAlchemy ORM 实例构造。"""
    model_config = {"from_attributes": True}

    id: int
    name: str
    category: str
    price: float
    stock: int
    status: str
    image_url: str
    color: str
    rating: float
    reviews: int
    shipping: str
    inventory_status: str = "unmonitored"

    @field_validator("status", mode="before")
    @classmethod
    def canonical_status(cls, value):
        # 旧库不需要破坏性迁移；响应先统一经营状态，后续编辑会保存规范值。
        return business_status(value)


class ProductListResponse(BaseModel):
    """商品分页响应：返回记录、总数和本页分页参数。"""
    items: list[Product]
    total: int
    page: int
    page_size: int


class ProductOptions(BaseModel):
    """筛选面板所需的去重分类、颜色和配送方式选项。"""
    categories: list[str]
    colors: list[str]
    shipping_options: list[str]
