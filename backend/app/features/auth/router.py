"""认证功能 API：注册首位管理员、登录、退出和读取当前账号。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import AUTH_COOKIE_SECURE
from app.db.session import get_db
from app.features.auth.models import UserModel
from app.features.auth.schemas import AuthStatusResponse, Credentials, UserResponse
from app.features.auth.service import AuthService, SESSION_TTL

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_NAME = "corelystics_session"


def current_user_from_request(request: Request, db: Session) -> UserModel | None:
    """认证功能 / 会话读取：从 HttpOnly Cookie 解析当前登录用户。"""
    return AuthService(db).get_user_for_token(request.cookies.get(COOKIE_NAME))


def require_current_user(
    request: Request, db: Annotated[Session, Depends(get_db)]
) -> UserModel:
    """商品管理 API 依赖：拒绝未登录请求。"""
    user = current_user_from_request(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please sign in")
    return user


def set_session_cookie(response: Response, token: str) -> None:
    """认证功能 / 登录成功：设置浏览器不可被 JavaScript 读取的会话 Cookie。"""
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(db: Annotated[Session, Depends(get_db)]) -> AuthStatusResponse:
    """注册页 / 初始化状态：告诉前端是否还需要创建首位管理员。"""
    return AuthStatusResponse(setup_required=AuthService(db).setup_required())


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_first_admin(
    credentials: Credentials,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> UserModel:
    """注册页 / 创建首位管理员；第二个账号起一律关闭注册。"""
    try:
        result = AuthService(db).register_first_admin(credentials.username, credentials.password)
    except IntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Admin account already exists") from error
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Admin account already exists")
    user, token = result
    set_session_cookie(response, token)
    return user


@router.post("/login", response_model=UserResponse)
def login(
    credentials: Credentials,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> UserModel:
    """登录页 / 校验账号密码，成功后建立 HttpOnly Cookie 会话。"""
    result = AuthService(db).login(credentials.username, credentials.password)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    user, token = result
    set_session_cookie(response, token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Annotated[Session, Depends(get_db)]) -> Response:
    """后台导航 / 退出登录：撤销服务端会话并清除浏览器 Cookie。"""
    AuthService(db).logout(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=AUTH_COOKIE_SECURE, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
def current_user(
    user: Annotated[UserModel, Depends(require_current_user)],
) -> UserModel:
    """应用入口 / 检查登录态：返回当前账号，供页面决定显示后台或登录页。"""
    return user
