"""认证功能业务：首位管理员初始化、登录会话签发与撤销。"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.auth.models import AuthSessionModel, UserModel
from app.features.auth.security import create_session_token, hash_password, hash_session_token, verify_password

SESSION_TTL = timedelta(days=7)


class AuthService:
    def __init__(self, db: Session):
        """绑定当前请求的数据库 Session，避免业务方法自行创建连接。"""
        self.db = db

    def setup_required(self) -> bool:
        """仅当还没有任何账号时开放首位管理员注册。"""
        return self.db.scalar(select(UserModel.id).limit(1)) is None

    def register_first_admin(self, username: str, password: str) -> tuple[UserModel, str] | None:
        """创建首位管理员并立即登录；有账号后返回 None，调用方转换为冲突响应。"""
        if not self.setup_required():
            return None
        # 固定首位账号主键为 1；即使两个首次注册请求同时到达，主键约束也只允许一个成功。
        user = UserModel(id=1, username=username, password_hash=hash_password(password))
        self.db.add(user)
        try:
            self.db.flush()
            token = self._create_session(user)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(user)
        return user, token

    def login(self, username: str, password: str) -> tuple[UserModel, str] | None:
        """检查账号密码并创建新的服务端登录会话。"""
        user = self.db.scalar(select(UserModel).where(UserModel.username == username))
        if user is None or not verify_password(password, user.password_hash):
            return None
        token = self._create_session(user)
        self.db.commit()
        return user, token

    def get_user_for_token(self, token: str | None) -> UserModel | None:
        """根据 Cookie 令牌找到未过期的登录账号。"""
        if not token:
            return None
        session = self.db.scalar(
            select(AuthSessionModel).where(
                AuthSessionModel.token_hash == hash_session_token(token),
                AuthSessionModel.expires_at > datetime.utcnow(),
            )
        )
        return self.db.get(UserModel, session.user_id) if session else None

    def logout(self, token: str | None) -> None:
        """撤销当前浏览器的服务端会话。"""
        if token:
            session = self.db.scalar(
                select(AuthSessionModel).where(AuthSessionModel.token_hash == hash_session_token(token))
            )
            if session:
                self.db.delete(session)
                self.db.commit()

    def _create_session(self, user: UserModel) -> str:
        """创建七天有效期的随机会话，并只把令牌摘要写入数据库。"""
        token = create_session_token()
        self.db.add(
            AuthSessionModel(
                user_id=user.id,
                token_hash=hash_session_token(token),
                expires_at=datetime.utcnow() + SESSION_TTL,
            )
        )
        return token
