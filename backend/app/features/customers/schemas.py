"""客户与收货地址 API 数据结构。"""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=32)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("name", "phone")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class CustomerUpdate(CustomerCreate):
    """完整替换客户资料时使用；字段约束与新增客户保持一致。"""
    pass


class Customer(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    phone: str
    email: str | None


class AddressFields(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=32)
    country: str = Field(default="中国", min_length=1, max_length=80)
    province: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=1, max_length=80)
    district: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=255)
    postal_code: str | None = Field(default=None, max_length=20)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    is_default: bool = False

    @field_validator("recipient_name", "phone", "country", "province", "city", "district", "detail")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value


class AddressCreate(AddressFields):
    customer_id: int = Field(gt=0)


class AddressUpdate(AddressFields):
    pass


class CustomerAddress(AddressFields):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
