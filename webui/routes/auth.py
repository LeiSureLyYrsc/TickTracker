"""认证相关 API 路由"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select

from ...database import get_session
from ...models import AuthSettings, LoginCode, User
from ..auth import (
    create_admin_token,
    create_user_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


class AdminLoginRequest(BaseModel):
    password: str


class UserLoginRequest(BaseModel):
    code: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


def get_jwt_secret(request: Request) -> str:
    return request.app.state.jwt_secret


async def get_current_user_payload(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    request: Request = None,
) -> dict:
    """验证 JWT token 并返回 payload"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
        )
    secret = request.app.state.jwt_secret
    payload = decode_token(credentials.credentials, secret)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
        )
    return payload


async def require_admin(payload: dict = Depends(get_current_user_payload)) -> dict:
    """需要管理员权限"""
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return payload


async def require_user(payload: dict = Depends(get_current_user_payload)) -> dict:
    """需要用户或管理员权限"""
    if payload.get("role") not in ("admin", "user"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要登录",
        )
    return payload


@router.post("/admin/login")
async def admin_login(body: AdminLoginRequest, request: Request):
    """管理员密码登录"""
    async with get_session() as session:
        result = await session.execute(
            select(AuthSettings).where(AuthSettings.id == 1)
        )
        auth = result.scalar_one_or_none()

    if not verify_password(body.password, auth.admin_password_hash if auth else ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="密码错误",
        )

    secret = request.app.state.jwt_secret
    token = create_admin_token(secret)
    return {"token": token, "role": "admin"}


@router.post("/user/login")
async def user_login(body: UserLoginRequest, request: Request):
    """用户验证码登录"""
    now = datetime.now()
    async with get_session() as session:
        result = await session.execute(
            select(LoginCode)
            .join(User, LoginCode.user_id == User.id)
            .where(
                LoginCode.code == body.code,
                LoginCode.is_used == False,
                LoginCode.expires_at > now,
            )
        )
        login_code = result.scalar_one_or_none()

        if not login_code:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="验证码无效或已过期",
            )

        # 标记验证码已使用
        login_code.is_used = True

        # 获取用户信息
        user_result = await session.execute(
            select(User).where(User.id == login_code.user_id)
        )
        user = user_result.scalar_one()

    secret = request.app.state.jwt_secret
    token = create_user_token(user.id, user.name, secret)
    return {"token": token, "role": "user", "user_id": user.id, "user_name": user.name}


@router.post("/admin/change-password")
async def change_admin_password(
    body: PasswordChangeRequest,
    request: Request,
    _: dict = Depends(require_admin),
):
    """管理员修改密码（WebUI 方式）"""
    async with get_session() as session:
        result = await session.execute(
            select(AuthSettings).where(AuthSettings.id == 1)
        )
        auth = result.scalar_one_or_none()

        if not verify_password(body.old_password, auth.admin_password_hash if auth else ""):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="原密码错误",
            )

        if len(body.new_password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新密码长度至少6位",
            )

        if auth:
            auth.admin_password_hash = hash_password(body.new_password)
        else:
            session.add(AuthSettings(id=1, admin_password_hash=hash_password(body.new_password)))

    return {"message": "密码已更新"}


@router.get("/me")
async def get_me(payload: dict = Depends(get_current_user_payload)):
    """获取当前登录信息"""
    return {
        "role": payload.get("role"),
        "sub": payload.get("sub"),
        "name": payload.get("name"),
    }
