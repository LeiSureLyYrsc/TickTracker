"""个人资料相关 API 路由（管理员与用户统一为 User 行）"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from ...database import get_session
from ...models import SystemSettings, User
from ..auth import hash_password, verify_password
from ..utils import audit, avatar_dir, get_client_ip
from .auth import require_user

router = APIRouter(prefix="/api", tags=["profile"])

ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


async def _get_me(session, payload: dict) -> User:
    user_id = int(payload.get("sub"))
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


async def _avatar_allowed(session) -> bool:
    result = await session.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        settings = SystemSettings(id=1)
        session.add(settings)
        await session.flush()
    return bool(settings.allow_avatar_upload)


class PasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str


@router.get("/me/profile")
async def get_my_profile(payload: dict = Depends(require_user)):
    """获取当前登录者资料"""
    from ..oidc import enabled_providers

    async with get_session() as session:
        avatar_allowed = await _avatar_allowed(session)
        result = await session.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )
        settings = result.scalar_one_or_none()
        if not settings:
            settings = SystemSettings(id=1)
            session.add(settings)
            await session.flush()
        user = await _get_me(session, payload)
        return {
            "role": user.role,
            "name": user.name,
            "user_id": user.id,
            "qq_id": user.qq_id,
            "email": user.email,
            "email_verified": user.email_verified,
            "has_avatar": bool(user.avatar_path),
            "avatar_url": f"/avatars/{user.avatar_path}" if user.avatar_path else None,
            "password_set": user.password_hash is not None,
            "avatar_upload_allowed": avatar_allowed,
            "allow_email_binding": bool(settings.allow_email_binding),
            "passkey_enabled": bool(settings.passkey_enabled),
            "oidc_enabled": len(enabled_providers()) > 0,
        }


@router.post("/me/password")
async def change_my_password(
    body: PasswordRequest,
    request: Request,
    payload: dict = Depends(require_user),
):
    """设置 / 修改密码"""
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    async with get_session() as session:
        ip = await get_client_ip(request, session)
        user = await _get_me(session, payload)
        if user.password_hash and not verify_password(
            body.old_password, user.password_hash
        ):
            raise HTTPException(status_code=401, detail="原密码错误")
        user.password_hash = hash_password(body.new_password)
        await audit(session, user.role, user.name, "修改密码", ip=ip)
    return {"message": "密码已更新"}


@router.post("/me/avatar")
async def upload_avatar(
    request: Request,
    payload: dict = Depends(require_user),
    file: UploadFile = ...,
):
    """上传头像"""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPG/GIF/WebP 图片")
    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="头像大小不能超过 2MB")

    async with get_session() as session:
        ip = await get_client_ip(request, session)
        if not await _avatar_allowed(session):
            raise HTTPException(status_code=403, detail="系统未开放头像上传")
        user = await _get_me(session, payload)

        identity = f"user_{user.id}"
        ext = ALLOWED_TYPES[file.content_type]
        filename = f"{identity}{ext}"
        # 删除旧头像（不同扩展名）
        d = avatar_dir()
        for old in d.glob(f"{identity}.*"):
            try:
                old.unlink()
            except OSError:
                pass
        (d / filename).write_bytes(data)
        user.avatar_path = filename
        await audit(session, user.role, user.name, "上传头像", ip=ip)

    return {"message": "头像已更新", "avatar_url": f"/avatars/{filename}"}


@router.delete("/me/avatar")
async def remove_avatar(payload: dict = Depends(require_user)):
    """删除头像（回退到 QQ 头像）"""
    async with get_session() as session:
        user = await _get_me(session, payload)
        if user.avatar_path:
            (avatar_dir() / user.avatar_path).unlink(missing_ok=True)
            user.avatar_path = None
    return {"message": "头像已删除"}


@router.get("/me/avatar")
async def get_my_avatar(payload: dict = Depends(require_user)):
    """获取当前登录者头像文件"""
    async with get_session() as session:
        user = await _get_me(session, payload)
        path = user.avatar_path
    if not path:
        raise HTTPException(status_code=404, detail="未设置头像")
    f = avatar_dir() / path
    if not f.exists():
        raise HTTPException(status_code=404, detail="头像文件不存在")
    return FileResponse(str(f))
