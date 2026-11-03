"""电子面单申请和人工核实的数据契约。"""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LabelCreate(BaseModel):
    carrier_name: str = Field(min_length=1, max_length=80)
    weight: Decimal = Field(gt=0, le=1000, decimal_places=3)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("carrier_name", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class LabelResolve(BaseModel):
    outcome: Literal["issued", "not_issued"]
    tracking_number: str = Field(default="", max_length=80)
    label_url: str = Field(default="", max_length=2000)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason", "tracking_number", "label_url", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def issued_fields(self):
        import re
        if self.outcome == "issued" and (not re.fullmatch(r"[A-Za-z0-9-]{6,80}", self.tracking_number) or not self.label_url):
            raise ValueError("已取号必须填写有效运单号和面单链接")
        return self
