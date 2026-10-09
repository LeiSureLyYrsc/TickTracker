"""Passkey（WebAuthn）通行密钥 API"""
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_, select

from ...database import get_session
from ...models import PasskeyCredential, SystemSettings, User
from ..auth import create_admin_token, create_user_token
from ..utils import audit, get_client_ip
from ..webauthn import (
    authentication_options,
    registration_options,
    verify_authentication,
    verify_registration,
)
from .auth import require_user

router = APIRouter(prefix="/api/passkey", tags=["passkey"])


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


def _resolve_site(request: Request, settings) -> tuple[str, str]:
    """返回 (rp_id, origin)"""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", "") or request.url.netloc
    hostname = host.split(":")[0]
    if not hostname:
        raise HTTPException(status_code=400, detail="无法识别站点域名")
    if scheme != "https" and not settings.passkey_allow_http:
        raise HTTPException(status_code=400, detail="Passkey 需要 HTTPS 访问，可在系统设置中开启 HTTP 测试")
    ids = json.loads(settings.passkey_rp_ids or "[]")
    if ids and hostname not in ids:
        raise HTTPException(status_code=400, detail=f"域名 {hostname} 未在允许列表中，请先在系统设置中添加")
    return hostname, f"{scheme}://{host}"


async def _get_me(session, payload: dict) -> User:
    user_id = int(payload.get("sub"))
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


async def _creds_for(session, user_id: int):
    result = await session.execute(
        select(PasskeyCredential)
        .where(PasskeyCredential.user_id == user_id)
        .order_by(PasskeyCredential.id.desc())
    )
    return result.scalars().all()


class LoginOptionsRequest(BaseModel):
    account: str = ""


class LoginVerifyRequest(BaseModel):
    account: str = ""
    credential: dict


class VerifyRequest(BaseModel):
    credential: dict


@router.get("/config")
async def passkey_config():
    """公共配置：是否启用"""
    async with get_session() as session:
        settings = await _get_settings(session)
        return {"enabled": bool(settings.passkey_enabled)}


@router.post("/register/options")
async def register_options(request: Request, payload: dict = Depends(require_user)):
    """生成注册选项（需已登录）"""
    async with get_session() as session:
        settings = await _get_settings(session)
        if not settings.passkey_enabled:
            raise HTTPException(status_code=403, detail="Passkey 功能未开启")
        rp_id, origin = _resolve_site(request, settings)
        user = await _get_me(session, payload)

        creds = await _creds_for(session, user.id)
        exclude = [c.credential_id for c in creds]

        options = registration_options(
            rp_id=rp_id,
            rp_name="代肝记录系统",
            user_name=user.name,
            user_handle_b64=base64.urlsafe_b64encode(str(user.id).encode()).rstrip(b"=").decode(),
            exclude_credential_ids=exclude,
        )
        return {"options": options}


@router.post("/register/verify")
async def register_verify(
    body: VerifyRequest, request: Request, payload: dict = Depends(require_user)
):
    """校验并保存新通行密钥"""
    async with get_session() as session:
        settings = await _get_settings(session)
        if not settings.passkey_enabled:
            raise HTTPException(status_code=403, detail="Passkey 功能未开启")
        rp_id, origin = _resolve_site(request, settings)

        credential = body.credential
        challenge = credential.get("response", {}).get("clientDataJSON")
        try:
            raw = base64.urlsafe_b64decode(challenge + "=" * (-len(challenge) % 4))
            cdata = json.loads(raw)
            expected_challenge = base64.urlsafe_b64decode(
                cdata["challenge"] + "=" * (-len(cdata["challenge"]) % 4)
            )
        except Exception:
            raise HTTPException(status_code=400, detail="无法解析挑战数据")

        try:
            result = verify_registration(credential, expected_challenge, rp_id, origin)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"校验失败：{e}")
        except Exception:
            raise HTTPException(status_code=400, detail="校验失败")

        user = await _get_me(session, payload)

        existing = await session.execute(
            select(PasskeyCredential).where(
                PasskeyCredential.credential_id == result["credential_id"]
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该通行密钥已注册")

        transports = credential.get("response", {}).get("transports")
        session.add(
            PasskeyCredential(
                user_id=user.id,
                is_admin=False,
                credential_id=result["credential_id"],
                public_key=result["public_key"],
                sign_count=result["sign_count"],
                transports=json.dumps(transports, ensure_ascii=False) if transports else None,
            )
        )
        await audit(
            session, user.role, user.name, "注册通行密钥",
            ip=await get_client_ip(request, session),
        )
    return {"message": "通行密钥已添加"}


@router.post("/login/options")
async def login_options(body: LoginOptionsRequest, request: Request):
    """生成认证选项"""
    async with get_session() as session:
        settings = await _get_settings(session)
        if not settings.passkey_enabled:
            raise HTTPException(status_code=403, detail="Passkey 功能未开启")
        rp_id, origin = _resolve_site(request, settings)

        # account 可选：为空时走可发现凭证（无用户名）流程；
        # 即便填了账号但查无此人，也返回空 allow 列表而不报错，
        # 以便浏览器/系统通行密钥选择器仍可工作，同时避免账号枚举。
        account = body.account.strip()
        user_id = None
        if account:
            result = await session.execute(
                select(User).where(or_(User.name == account, User.email == account))
            )
            user = result.scalar_one_or_none()
            if user:
                user_id = user.id

        if user_id is not None:
            creds = await _creds_for(session, user_id)
            allow = [c.credential_id for c in creds]
        else:
            allow = []
        options = authentication_options(rp_id=rp_id, allow_credential_ids=allow)
        return {"options": options}


@router.post("/login/verify")
async def login_verify(body: LoginVerifyRequest, request: Request):
    """校验认证并登录"""
    async with get_session() as session:
        settings = await _get_settings(session)
        if not settings.passkey_enabled:
            raise HTTPException(status_code=403, detail="Passkey 功能未开启")
        rp_id, origin = _resolve_site(request, settings)

        credential = body.credential
        try:
            challenge_b64 = credential.get("response", {}).get("clientDataJSON")
            raw = base64.urlsafe_b64decode(challenge_b64 + "=" * (-len(challenge_b64) % 4))
            cdata = json.loads(raw)
            expected_challenge = base64.urlsafe_b64decode(
                cdata["challenge"] + "=" * (-len(cdata["challenge"]) % 4)
            )
        except Exception:
            raise HTTPException(status_code=400, detail="无法解析挑战数据")

        credential_id_b64 = credential.get("id", "")
        result = await session.execute(
            select(PasskeyCredential).where(
                PasskeyCredential.credential_id == credential_id_b64
            )
        )
        stored = result.scalar_one_or_none()
        if not stored:
            raise HTTPException(status_code=400, detail="通行密钥不存在")

        try:
            new_sign_count, verified_id = verify_authentication(
                credential,
                expected_challenge,
                rp_id,
                origin,
                stored.public_key,
                stored.sign_count,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"校验失败：{e}")
        except Exception:
            raise HTTPException(status_code=400, detail="校验失败")

        stored.sign_count = new_sign_count

        result = await session.execute(select(User).where(User.id == stored.user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.login_disabled:
            raise HTTPException(status_code=403, detail="该账号已被停用 WebUI 登录，请联系管理员")

        if user.role == "admin":
            token = create_admin_token(user.id, user.name, request.app.state.jwt_secret)
        else:
            token = create_user_token(user.id, user.name, request.app.state.jwt_secret)

        await audit(
            session, user.role, user.name, "Passkey 登录",
            ip=await get_client_ip(request, session),
        )
    return {"token": token, "role": user.role, "user_id": user.id, "user_name": user.name}


@router.get("/credentials")
async def list_credentials(payload: dict = Depends(require_user)):
    """获取我的通行密钥列表"""
    async with get_session() as session:
        user = await _get_me(session, payload)
        creds = await _creds_for(session, user.id)
        return [
            {
                "id": c.id,
                "credential_id": c.credential_id[:16] + "...",
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in creds
        ]


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: int, payload: dict = Depends(require_user)):
    """删除我的通行密钥"""
    async with get_session() as session:
        user = await _get_me(session, payload)
        result = await session.execute(
            select(PasskeyCredential).where(PasskeyCredential.id == credential_id)
        )
        cred = result.scalar_one_or_none()
        if not cred or cred.user_id != user.id:
            raise HTTPException(status_code=404, detail="通行密钥不存在")
        await session.delete(cred)
    return {"message": "通行密钥已删除"}
