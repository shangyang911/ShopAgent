"""只持久化账号已读记录，待办本身始终来自真实业务状态。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.features.operations.models import utcnow


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
