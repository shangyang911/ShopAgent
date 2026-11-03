"""批量操作限制 100 件；库存、价格和删除不属于此接口的修改范围。"""
from typing import Literal
from pydantic import BaseModel, Field, PositiveInt


class BatchPreview(BaseModel):
    ids: list[PositiveInt] = Field(min_length=1, max_length=100)
    action: Literal["activate", "deactivate", "archive", "favorite", "unfavorite"]


class BatchApply(BatchPreview):
    snapshot: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=100)
