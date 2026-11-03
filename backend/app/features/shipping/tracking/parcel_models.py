"""附加包裹采用增量表，不改变历史主运单的主键和轨迹关系。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.features.operations.models import utcnow

class ParcelTrackingModel(Base):
    """每个附加包裹一份当前快照：最新进度、最近查询结果及并发查询的占用信息。"""
    __tablename__ = "parcel_tracking"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    package_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 标准快递编码+运单号唯一，防止别名公司名下重复轮询同一实际运单。
    identity_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    provider_state: Mapped[str | None] = mapped_column(String(20))  # 保留平台原始状态码，供规则核对。
    latest_event_at: Mapped[datetime | None] = mapped_column(DateTime)
    latest_description: Mapped[str | None] = mapped_column(Text)
    # 成功时间只在取得有效响应后更新；尝试时间包含失败，两者不能混用。
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    # 短期任务占用：时间决定是否仍在查询，令牌决定哪个返回结果有权写入。
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(500))
    shipment_revision: Mapped[int] = mapped_column(Integer, default=0)


class ParcelTrackingEvent(Base):
    """物流节点历史；实际发生时间可能未知，但本系统记录时间始终独立保留。"""
    __tablename__ = "parcel_tracking_events"
    __table_args__ = (UniqueConstraint("order_id", "package_no", "event_hash", name="uq_parcel_tracking_event"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    package_no: Mapped[int] = mapped_column(Integer, index=True)
    event_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw_time: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(250))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
