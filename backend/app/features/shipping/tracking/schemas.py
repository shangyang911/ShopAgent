from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_serializer


class TrackingSummary(BaseModel):
    """随运单返回的轻量摘要；完整节点时间线由单独的 tracking 接口读取。"""
    model_config = ConfigDict(from_attributes=True)
    status: str
    provider_state: str | None
    latest_event_at: datetime | None
    latest_description: str | None
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    next_sync_at: datetime | None
    error_code: str | None
    error_message: str | None

    @field_serializer("latest_event_at", "last_success_at", "last_attempt_at", "next_sync_at")
    def serialize_time(self, value):
        from app.features.operations.common import wire
        return wire(value)
