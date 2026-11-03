"""客服会话、待确认动作和人工工单，均绑定已登录管理员。"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.features.operations.models import utcnow


class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
    active_request: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentConversationArchive(Base):
    """使用独立表记录归档，不修改已有会话表结构或业务审计数据。"""
    __tablename__ = "agent_conversation_archives"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), primary_key=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentAction(Base):
    __tablename__ = "agent_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="cancel_order")
    state: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class AgentTicket(Base):
    __tablename__ = "agent_tickets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    transcript: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
