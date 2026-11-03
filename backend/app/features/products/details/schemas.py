"""Product-detail writes are structured content, never executable HTML."""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.features.products.catalog.schemas import Product


class DetailBlock(BaseModel):
    type: Literal['text', 'image']
    text: str = Field(default='', max_length=10000)
    image_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def valid_content(self):
        if self.type == 'text' and (not self.text.strip() or self.image_id is not None):
            raise ValueError('文字段落必须填写内容，不接受图片编号')
        if self.type == 'image' and (self.image_id is None or self.text):
            raise ValueError('图片段落必须选择图片，不接受文字内容')
        return self


class Parameter(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)

    @field_validator('name', 'value')
    @classmethod
    def required(cls, value):
        if not value.strip():
            raise ValueError('参数名和值不能空白')
        return value.strip()


class ImagePosition(BaseModel):
    id: int = Field(gt=0)
    alt: str = Field(default='', max_length=180)
    color: str = Field(default='', max_length=40)


class DetailWrite(BaseModel):
    expected_revision: int = Field(ge=1)
    description: list[DetailBlock] = Field(default_factory=list, max_length=100)
    parameters: list[Parameter] = Field(default_factory=list, max_length=100)
    gallery: list[ImagePosition] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def unique_fields(self):
        if len({row.id for row in self.gallery}) != len(self.gallery):
            raise ValueError('轮播图片不能重复')
        if len({row.name for row in self.parameters}) != len(self.parameters):
            raise ValueError('规格参数名不能重复')
        return self


class SKUWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    color: str = Field(default='', max_length=40)
    size: str = Field(default='', max_length=40)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    active: bool = True

    @field_validator('color', 'size')
    @classmethod
    def trim(cls, value):
        return value.strip()


class SKUUpdate(SKUWrite):
    expected_revision: int = Field(ge=1)


class StockTransfer(BaseModel):
    from_sku_id: int = Field(gt=0)
    to_sku_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1000000, strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=80)

    @field_validator('reason')
    @classmethod
    def required_reason(cls, value):
        if not value.strip():
            raise ValueError('请填写规格库存分配原因')
        return value.strip()

    @model_validator(mode='after')
    def different_skus(self):
        if self.from_sku_id == self.to_sku_id:
            raise ValueError('转出和转入 SKU 不能相同')
        return self


class ImageResponse(BaseModel):
    id: int | None
    url: str
    alt: str
    color: str
    position: int
    role: str


class SKUResponse(BaseModel):
    id: int
    product_id: int
    code: str
    color: str
    size: str
    price: Decimal
    stock: int
    reserved: int
    active: bool
    is_default: bool
    revision: int
    label: str
    available_quantity: int
    reserved_quantity: int
    on_hand_quantity: int


class DescriptionResponse(DetailBlock):
    url: str | None = None


class ProductDetail(Product):
    revision: int
    images: list[ImageResponse]
    media: list[ImageResponse]
    skus: list[SKUResponse]
    description: list[DescriptionResponse]
    parameters: list[Parameter]
    options: dict[str, list[str]]
