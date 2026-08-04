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
from .auth import require_admin, require_user

router = APIRouter(prefix="/api/oidc", tags=["oidc"])

# 内存 state / 一次性会话码 / 绑定码
_states: dict[str, dict] = {}
_sessions: dict[str, dict] = {}
_link_codes: dict[str, int] = {}

STATE_TTL = 600
SESSION_TTL = 300
LINK_CODE_TTL = 600


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


def _start_flow(request: Request, provider_id: str, mode: str = "login", user_id: int | None = None):
    """生成 OIDC 授权跳转（登录或绑定）"""
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
        "frontend_redirect": "",
        "mode": mode,
        "user_id": user_id,
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


@router.get("/login/{provider_id}")
async def oidc_login(provider_id: str, request: Request, redirect: str = ""):
    """发起 OIDC 登录，跳转到提供商"""
    return _start_flow(request, provider_id, mode="login")


@router.post("/link/start/{provider_id}")
async def oidc_link_start(provider_id: str, payload: dict = Depends(require_user)):
    """在当前账号下开始绑定 SSO 登录方式，返回跳转地址"""
    provider = _find_provider(provider_id)
    if not provider or not provider.get("enabled"):
        raise HTTPException(status_code=404, detail="登录方式不存在或未启用")
    if not provider.get("client_id") or not provider.get("authorization_endpoint"):
        raise HTTPException(status_code=400, detail="该登录方式配置不完整")

    code = secrets.token_urlsafe(24)
    _link_codes[code] = int(payload.get("sub"))
    return {"url": f"/api/oidc/link/{provider_id}?code={code}"}


@router.get("/link/{provider_id}")
async def oidc_link(provider_id: str, request: Request, code: str = ""):
    """校验绑定码后跳转提供商授权（浏览器导航，不带 Authorization 头）"""
    user_id = _link_codes.pop(code, None)
    if user_id is None:
        raise HTTPException(status_code=400, detail="绑定链接无效或已过期")
    return _start_flow(request, provider_id, mode="link", user_id=user_id)


@router.get("/my-bindings")
async def my_oidc_bindings(payload: dict = Depends(require_user)):
    """当前账号已绑定的 OIDC 提供商"""
    user_id = int(payload.get("sub"))
    providers = {p["id"]: p for p in load_providers()}
    async with get_session() as session:
        result = await session.execute(
            select(OidcBinding).where(OidcBinding.user_id == user_id)
        )
        return [
            {
                "provider_id": b.provider_id,
                "provider_name": providers.get(b.provider_id, {}).get("name", b.provider_id),
                "icon": providers.get(b.provider_id, {}).get("icon", "generic"),
                "icon_url": providers.get(b.provider_id, {}).get("icon_url"),
                "email": b.email,
                "name": b.name,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in result.scalars().all()
        ]


@router.delete("/bindings/{provider_id}")
async def unlink_oidc(provider_id: str, payload: dict = Depends(require_user)):
    """解绑当前账号下的 SSO 登录方式"""
    user_id = int(payload.get("sub"))
    async with get_session() as session:
        result = await session.execute(
            select(OidcBinding).where(
                OidcBinding.user_id == user_id, OidcBinding.provider_id == provider_id
            )
        )
        binding = result.scalar_one_or_none()
        if not binding:
            raise HTTPException(status_code=404, detail="未绑定该登录方式")
        await session.delete(binding)
    return {"message": "已解绑"}


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

    sub = str(claims.get("sub", ""))
    if not sub:
        raise HTTPException(status_code=400, detail="ID Token 缺少 sub")

    # 绑定模式：将 SSO 账号绑定到当前用户（不登录）
    if meta.get("mode") == "link":
        target_user_id = meta.get("user_id")
        email = claims.get("email")
        async with get_session() as session:
            existing = await session.execute(
                select(OidcBinding).where(
                    OidcBinding.provider_id == provider["id"], OidcBinding.sub == sub
                )
            )
            b = existing.scalar_one_or_none()
            if b and b.user_id != target_user_id:
                raise HTTPException(status_code=400, detail="该第三方账号已绑定其他用户")
            if not b:
                session.add(
                    OidcBinding(
                        provider_id=provider["id"],
                        sub=sub,
                        user_id=target_user_id,
                        email=email,
                        name=claims.get("name"),
                    )
                )
            await audit(
                session, "user", f"用户#{target_user_id}",
                "绑定 SSO", target=provider.get("name"), detail=f"sub={sub}",
            )
        return RedirectResponse("/oidc/link-callback?ok=1")

    # 关联 / 注册
    async with get_session() as session:
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
