"""OIDC 提供商配置与流程"""
import json
from pathlib import Path

import httpx

PROVIDERS_FILE_NAME = "oidc_providers.json"


def providers_file() -> Path:
    from nonebot import get_plugin_config

    from ..config import Config

    cfg = get_plugin_config(Config)
    return Path(cfg.commision_db_path).parent / PROVIDERS_FILE_NAME


def load_providers() -> list[dict]:
    f = providers_file()
    if f.exists():
        try:
            data = json.loads(f.read_text("utf-8"))
            return list(data.get("providers", []))
        except Exception:
            return []
    return []


def save_providers(providers: list[dict]) -> None:
    f = providers_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps({"providers": providers}, ensure_ascii=False, indent=2),
        "utf-8",
    )


def get_provider(provider_id: str) -> dict | None:
    for p in load_providers():
        if p.get("id") == provider_id:
            return p
    return None


def enabled_providers() -> list[dict]:
    return [p for p in load_providers() if p.get("enabled")]


async def discover_provider(issuer: str) -> dict:
    """从 OpenID 配置发现端点拉取配置。

    若输入已包含 .well-known 路径则直接作为请求 URL（兼容非标准 SSO），
    否则按标准拼接 /issuer/.well-known/openid-configuration。
    """
    base = issuer.strip()
    if ".well-known" in base:
        url = base
    else:
        url = f"{base.rstrip('/')}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        doc = r.json()
    return {
        "issuer": doc.get("issuer") or issuer,
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "token_endpoint": doc.get("token_endpoint"),
        "userinfo_endpoint": doc.get("userinfo_endpoint"),
        "jwks_uri": doc.get("jwks_uri"),
    }


async def verify_id_token(id_token: str, provider: dict) -> dict:
    """校验 ID Token 并返回 claims"""
    from authlib.jose import JsonWebToken

    client_id = provider.get("client_id", "")
    issuer = provider.get("issuer", "")
    jwks_uri = provider.get("jwks_uri")

    key = None
    if jwks_uri:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(jwks_uri)
            r.raise_for_status()
            key = r.json()
    else:
        key = provider.get("client_secret") or ""

    algs = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256", "HS384", "HS512"]
    jwt = JsonWebToken(algs)
    claims = jwt.decode(id_token, key=key)
    claims.validate()

    aud = claims.get("aud")
    if isinstance(aud, list):
        if client_id not in aud:
            raise ValueError("invalid audience")
    elif aud != client_id:
        raise ValueError("invalid audience")
    if issuer and claims.get("iss") != issuer:
        raise ValueError("invalid issuer")
    return dict(claims)
