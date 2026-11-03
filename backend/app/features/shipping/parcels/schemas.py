"""包裹维护与运费结算的数据契约。"""
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.features.shipping.fulfillment.schemas import ShipmentWrite


class ParcelWrite(ShipmentWrite):
    pass


class FreightWrite(BaseModel):
    actual_cost: Decimal = Field(ge=0, le=Decimal("99999999.99"), decimal_places=2)
    source: str = Field(min_length=1, max_length=250)
    expected_revision: int = Field(ge=0)

    @field_validator("source", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value
