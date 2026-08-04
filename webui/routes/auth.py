"""认证相关 API 路由"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import or_, select

from ...database import get_session
from ...models import LoginCode, SystemSettings, User
from ..auth import (
    create_admin_token,
    create_user_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..utils import (
    audit,
    check_and_bump_counter,
    get_client_ip,
    is_counter_locked,
    reset_counter,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


class AdminLoginRequest(BaseModel):
    password: str


class UserLoginRequest(BaseModel):
    code: str


class AccountLoginRequest(BaseModel):
    account: str
    password: str


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


@router.post("/login")
async def account_login(body: AccountLoginRequest, request: Request):
    """账号 + 密码登录（管理员用户名固定为 admin）"""
    from nonebot import get_plugin_config

    from ...config import Config

    cfg = get_plugin_config(Config)
    account = body.account.strip()

    async with get_session() as session:
        ip = await get_client_ip(request, session)

        # 失败锁定预检
        if cfg.login_max_failures > 0 and await is_counter_locked(
            session, "login_fail", account
        ):
            await audit(session, "system", account, "登录被锁定拦截", ip=ip)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="尝试次数过多，账号已被临时锁定，请稍后再试",
            )

        role = None
        user_id = None
        user_name = account

        result = await session.execute(
            select(User).where(or_(User.name == account, User.email == account))
        )
        user = result.scalar_one_or_none()
        if user and user.login_disabled:
            await audit(session, "system", account, "登录被停用拦截", ip=ip)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该账号已被停用 WebUI 登录，请联系管理员",
            )
        valid = (
            user is not None
            and user.password_hash is not None
            and verify_password(body.password, user.password_hash)
        )
        if valid:
            role = user.role
            user_id = user.id
            user_name = user.name

        if not valid:
            if cfg.login_max_failures > 0:
                await check_and_bump_counter(
                    session,
                    "login_fail",
                    account,
                    cfg.login_max_failures,
                    cfg.login_lock_minutes * 60,
                    cfg.login_lock_minutes * 60,
                )
            await audit(
                session,
                "system",
                account,
                "账号登录失败",
                detail="密码错误",
                ip=ip,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="账号或密码错误",
            )

        if cfg.login_max_failures > 0:
            await reset_counter(session, "login_fail", account)
        await audit(
            session,
            role,
            user_name,
            "账号登录",
            target=f"用户#{user_id}" if user_id is not None else None,
            detail="登录成功",
            ip=ip,
        )

    secret = request.app.state.jwt_secret
    if role == "admin":
        token = create_admin_token(user_id, user_name, secret)
    else:
        token = create_user_token(user_id, user_name, secret)
    return {"token": token, "role": role, "user_id": user_id, "user_name": user_name}


@router.post("/admin/login")
async def admin_login(body: AdminLoginRequest, request: Request):
    """管理员密码登录（兼容旧接口）"""
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        admin = result.scalars().first()

        ip = await get_client_ip(request, session)
        valid = verify_password(
            body.password, admin.password_hash if admin else ""
        )

        if not valid:
            await audit(
                session,
                "admin",
                "unknown",
                "管理员登录",
                detail="密码错误",
                ip=ip,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="密码错误",
            )

        await audit(
            session,
            "admin",
            admin.name,
            "管理员登录",
            detail="登录成功",
            ip=ip,
        )

    secret = request.app.state.jwt_secret
    token = create_admin_token(admin.id, admin.name, secret)
    return {"token": token, "role": "admin", "user_id": admin.id, "user_name": admin.name}


@router.post("/user/login")
async def user_login(body: UserLoginRequest, request: Request):
    """用户验证码登录"""
    from nonebot import get_plugin_config

    from ...config import Config

    cfg = get_plugin_config(Config)
    now = datetime.now()
    async with get_session() as session:
        ip = await get_client_ip(request, session)

        # 验证码登录按 IP 限流
        if cfg.login_code_ip_limit > 0 and await check_and_bump_counter(
            session,
            "code_ip",
            ip,
            cfg.login_code_ip_limit,
            cfg.login_code_ip_window,
        ):
            await audit(
                session,
                "system",
                ip,
                "验证码登录被限流拦截",
                ip=ip,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="验证码请求过于频繁，请稍后再试",
            )

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
            await audit(
                session,
                "user",
                "unknown",
                "用户登录",
                detail="验证码无效或已过期",
                ip=ip,
            )
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="验证码无效或已过期",
            )

        # 获取用户信息
        user_result = await session.execute(
            select(User).where(User.id == login_code.user_id)
        )
        user = user_result.scalar_one()

        if user.login_disabled:
            await audit(session, "user", user.name, "登录被停用拦截", ip=ip)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该账号已被停用 WebUI 登录，请联系管理员",
            )

        # 标记验证码已使用
        login_code.is_used = True

        await audit(
            session,
            user.role,
            user.name,
            "用户登录",
            target=f"用户#{user.id}",
            detail=f"QQ:{user.qq_id or '未绑定'}",
            ip=ip,
        )

    secret = request.app.state.jwt_secret
    if user.role == "admin":
        token = create_admin_token(user.id, user.name, secret)
    else:
        token = create_user_token(user.id, user.name, secret)
    return {
        "token": token,
        "role": user.role,
        "user_id": user.id,
        "user_name": user.name,
    }


@router.post("/admin/change-password")
async def change_admin_password(
    body: PasswordChangeRequest,
    request: Request,
    _: dict = Depends(require_admin),
):
    """管理员修改密码（兼容旧接口）"""
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        admin = result.scalars().first()

        if not verify_password(body.old_password, admin.password_hash if admin else ""):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="原密码错误",
            )

        if len(body.new_password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="新密码长度至少6位",
            )

        if admin:
            admin.password_hash = hash_password(body.new_password)

        ip = await get_client_ip(request, session)
        await audit(
            session,
            "admin",
            "admin",
            "修改管理员密码",
            ip=ip,
        )

    return {"message": "密码已更新"}


@router.get("/config")
async def public_auth_config():
    """公开登录配置（忘记密码开关 / Passkey / OIDC 提供商）"""
    from ..oidc import enabled_providers

    async with get_session() as session:
        s_result = await session.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )
        settings = s_result.scalar_one_or_none()
        return {
            "allow_forgot_password": bool(settings and settings.allow_forgot_password),
            "passkey_enabled": bool(settings and settings.passkey_enabled),
            "oidc_providers": [
                {
                    "id": p.get("id"),
                    "name": p.get("name", "OIDC"),
                    "icon": p.get("icon", "generic"),
                    "icon_url": p.get("icon_url"),
                }
                for p in enabled_providers()
            ],
        }


@router.get("/me")
async def get_me(payload: dict = Depends(get_current_user_payload)):
    """获取当前登录信息"""
    return {
        "role": payload.get("role"),
        "sub": payload.get("sub"),
        "name": payload.get("name"),
    }
