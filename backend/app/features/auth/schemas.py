"""认证功能 API 数据结构：校验注册、登录请求并定义账号响应。"""
from datetime import datetime

from pydantic import BaseModel, Field


class Credentials(BaseModel):
    """登录/首次注册凭据；用户名限定为 URL 和表单友好的 ASCII 字符。"""
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)


class UserResponse(BaseModel):
    """返回给前端的公开账号字段，不包含密码哈希或会话令牌。"""
    model_config = {"from_attributes": True}

    id: int
    username: str
    created_at: datetime


class AuthStatusResponse(BaseModel):
    """注册页使用的初始化状态；true 表示尚未创建首位管理员。"""
    setup_required: bool
