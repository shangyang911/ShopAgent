"""跟进资料独立建表，不改写已有客户资料和历史订单。"""
from sqlalchemy import ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
