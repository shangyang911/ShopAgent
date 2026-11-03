"""消息列表的筛选与已读输入契约。"""
from typing import Literal
from pydantic import BaseModel, Field

Category = Literal["all", "stock", "shipment", "aftersale"]
ReadState = Literal["all", "unread", "read"]


class ReadWrite(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=200)
