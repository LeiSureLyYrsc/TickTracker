"""OIDC 第三方登录 API"""
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from ...database import get_session
from ...models import OidcBinding, User
from ..auth import create_admin_token, create_user_token
from ..oidc import (
    discover_provider,
    enabled_providers,
    get_provider,
    load_providers,
    save_providers,
    verify_id_token,
)
from ..utils import audit, get_client_ip
from .auth import require_admin

router = APIRouter(prefix="/api/oidc", tags=["oidc"])

# 内存 state / 一次性会话码
_states: dict[str, dict] = {}
_sessions: dict[str, dict] = {}

STATE_TTL = 600
SESSION_TTL = 300


class ProviderRequest(BaseModel):
    provider: dict


class DiscoverRequest(BaseModel):
    issuer: str


class ConsumeRequest(BaseModel):
    code: str


def _find_provider(provider_id: str) -> dict | None:
    return get_provider(provider_id)


# ---- 公共登录 ----

@router.get("/providers")
async def public_providers():
    """返回已启用的提供商列表（供登录页展示）"""
    return [
        {
            "id": p.get("id"),
            "name": p.get("name", "OIDC"),
            "icon": p.get("icon", "generic"),
            "icon_url": p.get("icon_url"),
        }
        for p in enabled_providers()
    ]


@router.get("/login/{provider_id}")
async def oidc_login(provider_id: str, request: Request, redirect: str = ""):
    """发起 OIDC 登录，跳转到提供商"""
    provider = _find_provider(provider_id)
    if not provider or not provider.get("enabled"):
        raise HTTPException(status_code=404, detail="登录方式不存在或未启用")
    if not provider.get("client_id") or not provider.get("authorization_endpoint"):
        raise HTTPException(status_code=400, detail="该登录方式配置不完整")

    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/api/oidc/callback"
    state = secrets.token_urlsafe(24)
    _states[state] = {
        "provider_id": provider_id,
        "redirect_uri": redirect_uri,
        "frontend_redirect": redirect,
        "exp": time.time() + STATE_TTL,
    }

    params = {
        "response_type": "code",
        "client_id": provider["client_id"],
        "redirect_uri": redirect_uri,
        "scope": provider.get("scopes") or "openid email profile",
        "state": state,
    }
    url = provider["authorization_endpoint"] + ("&" if "?" in provider["authorization_endpoint"] else "?") + urlencode(params)
    return RedirectResponse(url)


@router.get("/callback")
async def oidc_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """OIDC 回调"""
    if error:
        raise HTTPException(status_code=400, detail=f"登录失败：{error_description or error}")
    meta = _states.pop(state, None)
    if not meta:
        raise HTTPException(status_code=400, detail="登录状态无效或已过期")
    provider = _find_provider(meta["provider_id"])
    if not provider:
        raise HTTPException(status_code=400, detail="登录方式不存在")

    # 交换 code
    token_params = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": meta["redirect_uri"],
        "client_id": provider.get("client_id", ""),
        "client_secret": provider.get("client_secret", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.post(provider["token_endpoint"], data=token_params)
            r.raise_for_status()
            token_data = r.json()
    except Exception:
        raise HTTPException(status_code=400, detail="令牌交换失败")

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="提供商未返回 ID Token")

    try:
        claims = await verify_id_token(id_token, provider)
    except Exception:
        raise HTTPException(status_code=400, detail="ID Token 校验失败")

    # 关联 / 注册
    async with get_session() as session:
        sub = str(claims.get("sub", ""))
        if not sub:
            raise HTTPException(status_code=400, detail="ID Token 缺少 sub")
        result = await session.execute(
            select(OidcBinding).where(
                OidcBinding.provider_id == provider["id"], OidcBinding.sub == sub
            )
        )
        binding = result.scalar_one_or_none()

        if binding:
            user = await session.execute(select(User).where(User.id == binding.user_id))
            u = user.scalar_one_or_none()
            if not u:
                raise HTTPException(status_code=400, detail="绑定的用户不存在")
            if u.login_disabled:
                raise HTTPException(status_code=403, detail="该账号已被停用 WebUI 登录，请联系管理员")
            if u.role == "admin":
                token = create_admin_token(u.id, u.name, _jwt_secret())
            else:
                token = create_user_token(u.id, u.name, _jwt_secret())
            role, name, user_id = u.role, u.name, u.id
            await audit(session, role, name, "OIDC 登录", target=provider.get("name"), detail="已绑定账号")
        else:
            if not provider.get("allow_register"):
                raise HTTPException(status_code=403, detail="该登录方式未开放注册，请联系管理员绑定账号")
            role, name, user_id, token = await _register_from_claims(session, provider, claims)
            await audit(session, role, name, "OIDC 注册", target=provider.get("name"), detail=f"sub={sub}")

    sc = secrets.token_urlsafe(24)
    _sessions[sc] = {
        "token": token,
        "role": role,
        "user_name": name,
        "user_id": user_id,
        "exp": time.time() + SESSION_TTL,
    }
    target = meta.get("frontend_redirect") or "/oidc/callback"
    return RedirectResponse(f"{target}?code={sc}")


def _jwt_secret():
    from nonebot import get_plugin_config

    from ...config import Config

    return get_plugin_config(Config).commision_tracker_jwt_secret


async def _register_from_claims(session, provider: dict, claims: dict):
    """OIDC 注册新用户并返回 (role, name, user_id, token)"""
    email = claims.get("email")
    email_verified = bool(claims.get("email_verified"))
    name = (
        claims.get("name")
        or claims.get("preferred_username")
        or (email.split("@")[0] if email else f"oidc_{str(claims.get('sub'))[:8]}")
    )
    base = re.sub(r"[^\w\u4e00-\u9fff-]", "", str(name))[:32] or "user"
    username = base
    i = 1
    while True:
        r = await session.execute(select(User).where(User.name == username))
        if not r.scalar_one_or_none():
            break
        username = f"{base}{i}"
        i += 1

    user = User(name=username)
    if email:
        r = await session.execute(select(User).where(User.email == email))
        if not r.scalar_one_or_none():
            user.email = email
            user.email_verified = email_verified
    session.add(user)
    await session.flush()

    session.add(
        OidcBinding(
            provider_id=provider["id"],
            sub=str(claims.get("sub")),
            user_id=user.id,
            email=email,
            name=username,
        )
    )
    await session.flush()
    token = create_user_token(user.id, username, _jwt_secret())
    return "user", username, user.id, token


@router.post("/consume")
async def oidc_consume(body: ConsumeRequest):
    """用一次性会话码换取登录信息"""
    data = _sessions.pop(body.code, None)
    if not data:
        raise HTTPException(status_code=400, detail="会话码无效或已过期")
    return {
        "token": data["token"],
        "role": data["role"],
        "user_name": data["user_name"],
        "user_id": data["user_id"],
    }


# ---- 管理员配置 ----

@router.get("/providers/manage")
async def manage_list(_: dict = Depends(require_admin)):
    return {"providers": load_providers()}


@router.post("/providers/manage")
async def manage_create(body: ProviderRequest, _: dict = Depends(require_admin)):
    providers = load_providers()
    provider = body.provider
    if not provider.get("id"):
        provider["id"] = secrets.token_hex(4)
    if any(p.get("id") == provider.get("id") for p in providers):
        raise HTTPException(status_code=400, detail="提供商 ID 已存在")
    providers.append(provider)
    save_providers(providers)
    return {"message": "提供商已添加", "id": provider.get("id")}


@router.put("/providers/manage/{provider_id}")
async def manage_update(provider_id: str, body: ProviderRequest, _: dict = Depends(require_admin)):
    providers = load_providers()
    for i, p in enumerate(providers):
        if p.get("id") == provider_id:
            updated = body.provider
            updated["id"] = provider_id
            providers[i] = updated
            save_providers(providers)
            return {"message": "提供商已更新"}
    raise HTTPException(status_code=404, detail="提供商不存在")


@router.delete("/providers/manage/{provider_id}")
async def manage_delete(provider_id: str, _: dict = Depends(require_admin)):
    providers = load_providers()
    remaining = [p for p in providers if p.get("id") != provider_id]
    if len(remaining) == len(providers):
        raise HTTPException(status_code=404, detail="提供商不存在")
    save_providers(remaining)
    return {"message": "提供商已删除"}


@router.post("/discover")
async def discover(body: DiscoverRequest, _: dict = Depends(require_admin)):
    """从 .well-known 发现并返回端点配置"""
    try:
        data = await discover_provider(body.issuer.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="无法获取 .well-known/openid-configuration，请检查 Issuer 地址")
    return data
