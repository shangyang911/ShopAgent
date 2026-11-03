"""售后输入契约：金额使用 Decimal，实际发生时间必须自带时区。"""
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator


class ReturnRequest(BaseModel):
    order_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=10000, strict=True)


class CaseCreate(BaseModel):
    kind: Literal["refund", "return_refund"]
    requested_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str = Field(min_length=1, max_length=2000)
    items: list[ReturnRequest] = Field(default_factory=list, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value):
        if not value.strip():
            raise ValueError("请填写售后原因")
        return value.strip()

    @model_validator(mode="after")
    def validate_lines(self):
        # 仅退款不能夹带退货；同一行重复提交会掩盖数量核对，因此直接拒绝。
        if (self.kind == "return_refund") != bool(self.items):
            raise ValueError("退货退款必须选择退货商品，仅退款无需商品明细")
        if len({row.order_item_id for row in self.items}) != len(self.items):
            raise ValueError("退货商品不能重复")
        return self


class CaseVersion(BaseModel):
    expected_revision: int = Field(ge=1)


class RefundWrite(CaseVersion):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reference: str = Field(min_length=1, max_length=250)
    occurred_at: AwareDatetime
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("reference")
    @classmethod
    def reference_required(cls, value):
        if not value.strip():
            raise ValueError("请填写退款凭据或外部交易流水号")
        return value.strip()


class InspectionLine(BaseModel):
    order_item_id: int = Field(gt=0)
    accepted_quantity: int = Field(ge=0, le=10000, strict=True)
    damaged_quantity: int = Field(ge=0, le=10000, strict=True)


class InspectionWrite(CaseVersion):
    items: list[InspectionLine] = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def note_required(cls, value):
        if not value.strip():
            raise ValueError("请填写验收说明")
        return value.strip()
