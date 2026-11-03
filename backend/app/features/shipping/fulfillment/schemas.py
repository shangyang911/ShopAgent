"""运单登记、交接与批量操作的数据契约。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.features.shipping.tracking.schemas import TrackingSummary


class ShipmentWrite(BaseModel):
    carrier_name: str = Field(min_length=1, max_length=80)
    tracking_number: str = Field(min_length=6, max_length=80)
    remark: str = Field(default="", max_length=1000)
    expected_revision: int = Field(ge=0)

    @field_validator("carrier_name", "tracking_number", "remark", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("tracking_number")
    @classmethod
    def tracking_format(cls, value):
        # 只做基础格式校验；人工登记不能宣称已在快递公司验证单号有效。
        import re
        if not re.fullmatch(r"[A-Za-z0-9-]+", value):
            raise ValueError("运单号只能包含英文字母、数字和连字符")
        return value


class ShipmentDispatch(BaseModel):
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100)


class ShipmentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_id: int
    carrier_name: str
    tracking_number: str
    remark: str
    revision: int
    historical: bool
    updated_by: str
    updated_at: datetime
    dispatched_by: str | None
    dispatched_at: datetime | None
    tracking: TrackingSummary | None = None

    @field_serializer("updated_at", "dispatched_at")
    def serialize_time(self, value):
        from app.features.operations.common import wire
        return wire(value)


class BatchItem(BaseModel):
    order_id: int = Field(gt=0)
    expected_revision: int = Field(ge=1)


class BatchDispatch(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=80)

    @model_validator(mode="after")
    def unique_orders(self):
        if len({item.order_id for item in self.items}) != len(self.items):
            raise ValueError("批量发货不能包含重复订单")
        return self
