"""盘点填实盘在库数，报损和更正填差额，两类输入不得混用。"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AdjustmentItem(BaseModel):
    product_id: int = Field(gt=0)
    sku_id: int | None = Field(default=None, gt=0)
    counted_on_hand: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    available_delta: int | None = Field(default=None, ge=-1000000, le=1000000, strict=True)


class AdjustmentCreate(BaseModel):
    kind: Literal["count", "damage", "correction"]
    reason: str = Field(min_length=1, max_length=2000)
    source_inbound_id: int | None = Field(default=None, gt=0)
    items: list[AdjustmentItem] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("请填写盘点、报损或更正原因")
        return value.strip()

    @model_validator(mode="after")
    def check_items(self):
        if len({row.product_id for row in self.items}) != len(self.items):
            raise ValueError("同一商品不能重复填写")
        if (self.kind == "correction") != (self.source_inbound_id is not None):
            raise ValueError("入库更正必须关联原已确认入库单，其他调整无需原入库单")
        for row in self.items:
            if self.kind == "count":
                if row.counted_on_hand is None or row.available_delta is not None:
                    raise ValueError("盘点只填写实盘在库数量")
            elif row.counted_on_hand is not None or not row.available_delta:
                raise ValueError("报损或更正只填写非零库存差额")
            if self.kind == "damage" and row.available_delta >= 0:
                raise ValueError("报损差额必须小于零")
        return self
