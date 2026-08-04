"""WebUI 通用工具：客户端 IP 获取与审计日志"""
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Request
from sqlalchemy import select

from ..models import AuditLog, SecurityCounter, SystemSettings


async def is_reverse_proxy(session) -> bool:
    """读取系统设置中的反向代理开关"""
    result = await session.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    settings = result.scalar_one_or_none()
    return bool(settings and settings.reverse_proxy)


async def get_client_ip(request: Request, session) -> str:
    """获取客户端 IP。

    反向代理模式开启时，优先取 X-Forwarded-For 中的首个 IP 或 X-Real-IP，
    否则回退到连接地址 request.client.host。
    """
    if await is_reverse_proxy(session):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        x_real = request.headers.get("x-real-ip")
        if x_real:
            return x_real.strip()
    if request.client:
        return request.client.host
    return "unknown"


async def audit(
    session,
    actor_type: str,
    actor_name: str,
    action: str,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """写入一条审计日志（加入当前会话，随事务一并提交）"""
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_name=actor_name,
            action=action,
            target=target,
            detail=detail,
            ip=ip,
        )
    )


async def check_and_bump_counter(
    session,
    kind: str,
    key: str,
    limit: int,
    window_seconds: int,
    lock_seconds: int = 0,
) -> bool:
    """安全计数：返回是否被限制（达到上限）。

    - window_seconds：计数窗口，窗口到期自动重置。
    - lock_seconds：达到上限后的锁定长度；为 0 时锁定至窗口结束。
    """
    now = datetime.now()
    result = await session.execute(
        select(SecurityCounter).where(
            SecurityCounter.kind == kind, SecurityCounter.key == key
        )
    )
    row = result.scalar_one_or_none()

    if row and row.locked_until and now < row.locked_until:
        return True

    if row and row.window_start and (now - row.window_start).total_seconds() < window_seconds:
        row.count += 1
    else:
        if row:
            row.count = 1
            row.window_start = now
            row.locked_until = None
        else:
            row = SecurityCounter(
                kind=kind, key=key, count=1, window_start=now
            )
            session.add(row)

    blocked = row.count >= limit
    if blocked:
        if lock_seconds > 0:
            row.locked_until = now + timedelta(seconds=lock_seconds)
        else:
            row.locked_until = now + timedelta(seconds=window_seconds)
    return blocked


async def is_counter_locked(session, kind: str, key: str) -> bool:
    """仅检查是否处于锁定状态（不修改计数）"""
    result = await session.execute(
        select(SecurityCounter).where(
            SecurityCounter.kind == kind, SecurityCounter.key == key
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return False
    now = datetime.now()
    if row.locked_until and now < row.locked_until:
        return True
    return False


async def reset_counter(session, kind: str, key: str) -> None:
    """清除安全计数（登录成功时调用）"""
    result = await session.execute(
        select(SecurityCounter).where(
            SecurityCounter.kind == kind, SecurityCounter.key == key
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.count = 0
        row.locked_until = None
        row.window_start = datetime.now()


def avatar_dir() -> Path:
    """头像存储目录（与数据库同级 data/avatars/）"""
    from nonebot import get_plugin_config

    from ..config import Config

    cfg = get_plugin_config(Config)
    db_dir = Path(cfg.commision_db_path).parent
    d = db_dir / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    return d
