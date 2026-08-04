"""邮箱绑定与忘记密码 API"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select

from ...database import get_session
from ...models import SystemSettings, User
from ..auth import hash_password
from ..email import (
    EmailNotConfiguredError,
    issue_email_code,
    send_email,
    verify_email_code,
)
from ..utils import audit, get_client_ip
from .auth import require_user

router = APIRouter(prefix="/api", tags=["email"])


async def _get_settings(session):
    result = await session.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    s = result.scalar_one_or_none()
    if not s:
        s = SystemSettings(id=1)
        session.add(s)
        await session.flush()
    return s


def _validate_email(email: str) -> str:
    e = email.strip().lower()
    if "@" not in e or "." not in e.split("@")[-1] or len(e) < 5:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return e


class BindEmailRequest(BaseModel):
    email: str


class VerifyEmailRequest(BaseModel):
    email: str
    code: str


class ForgotSendRequest(BaseModel):
    account: str


class ForgotResetRequest(BaseModel):
    account: str
    code: str
    new_password: str


async def _get_me(session, payload: dict) -> User:
    user_id = int(payload.get("sub"))
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


async def _email_taken(session, email: str, exclude_id: int | None = None) -> bool:
    query = select(User).where(User.email == email)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


@router.post("/me/email/bind")
async def bind_email(body: BindEmailRequest, request: Request, payload: dict = Depends(require_user)):
    """发送邮箱绑定验证码"""
    email = _validate_email(body.email)
    async with get_session() as session:
        settings = await _get_settings(session)
        if not settings.allow_email_binding:
            raise HTTPException(status_code=403, detail="系统未开放邮箱绑定")
        user = await _get_me(session, payload)
        if await _email_taken(session, email, exclude_id=user.id):
            raise HTTPException(status_code=400, detail="该邮箱已被其他账号绑定")
        if user.email:
            raise HTTPException(status_code=400, detail="当前已绑定邮箱，如需更换请联系管理员")

        code = await issue_email_code(session, email, "bind")
        await audit(
            session, user.role, user.name, "申请邮箱绑定", target=email,
            ip=await get_client_ip(request, session),
        )

    try:
        await send_email(email, "代肝记录系统 - 邮箱绑定验证码", f"您的绑定验证码为：{code}，10 分钟内有效。")
    except EmailNotConfiguredError:
        raise HTTPException(status_code=500, detail="邮箱服务未配置")
    except Exception:
        raise HTTPException(status_code=500, detail="邮件发送失败，请检查 SMTP 配置")
    return {"message": "验证码已发送至邮箱"}


@router.post("/me/email/verify")
async def verify_email(body: VerifyEmailRequest, request: Request, payload: dict = Depends(require_user)):
    """校验并完成邮箱绑定"""
    email = _validate_email(body.email)
    async with get_session() as session:
        if not await verify_email_code(session, email, "bind", body.code.strip()):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        user = await _get_me(session, payload)
        user.email = email
        user.email_verified = True
        await audit(
            session, user.role, user.name, "绑定邮箱", target=email,
            ip=await get_client_ip(request, session),
        )
    return {"message": "邮箱绑定成功"}


@router.post("/auth/forgot/send")
async def forgot_send(body: ForgotSendRequest, request: Request):
    """发送密码重置验证码"""
    account = body.account.strip()
    code = None
    email = None
    async with get_session() as session:
        settings = await _get_settings(session)
        if not settings.allow_forgot_password:
            raise HTTPException(status_code=403, detail="系统未开放忘记密码功能")

        result = await session.execute(
            select(User).where(or_(User.name == account, User.email == account))
        )
        user = result.scalar_one_or_none()
        if user and user.email_verified:
            email = user.email

        if email:
            code = await issue_email_code(session, email, "reset")
            await audit(
                session, "system", account, "申请密码重置", target=email,
                ip=await get_client_ip(request, session),
            )

    if code:
        try:
            await send_email(email, "代肝记录系统 - 密码重置", f"您的密码重置验证码为：{code}，10 分钟内有效。")
        except EmailNotConfiguredError:
            raise HTTPException(status_code=500, detail="邮箱服务未配置")
        except Exception:
            raise HTTPException(status_code=500, detail="邮件发送失败，请检查 SMTP 配置")
    # 无论账号是否存在都返回成功，避免账号探测
    return {"message": "若该账号绑定了已验证邮箱，重置验证码已发送"}


@router.post("/auth/forgot/reset")
async def forgot_reset(body: ForgotResetRequest, request: Request):
    """使用验证码重置密码"""
    account = body.account.strip()
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")

    async with get_session() as session:
        result = await session.execute(
            select(User).where(or_(User.name == account, User.email == account))
        )
        user = result.scalar_one_or_none()
        email = user.email if (user and user.email_verified) else None

        if not email or not await verify_email_code(session, email, "reset", body.code.strip()):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")

        user.password_hash = hash_password(body.new_password)
        await audit(
            session, "system", account, "重置密码",
            ip=await get_client_ip(request, session),
        )
    return {"message": "密码已重置，请使用新密码登录"}
