"""数据库引擎与会话工厂"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

_engine = None
_async_session_factory = None

# 针对已存在表的增量迁移：表名 -> [(列名, 列定义)]
COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("role", "VARCHAR(16) DEFAULT 'user'"),
        ("password_hash", "VARCHAR(256)"),
        ("email", "VARCHAR(128)"),
        ("email_verified", "BOOLEAN DEFAULT 0"),
        ("avatar_path", "VARCHAR(128)"),
        ("login_disabled", "BOOLEAN DEFAULT 0"),
    ],
    "auth_settings": [
        ("admin_name", "VARCHAR(64) DEFAULT 'admin'"),
        ("admin_avatar_path", "VARCHAR(128)"),
        ("admin_email", "VARCHAR(128)"),
        ("admin_email_verified", "BOOLEAN DEFAULT 0"),
    ],
    "system_settings": [
        ("allow_avatar_upload", "BOOLEAN DEFAULT 0"),
        ("smtp_host", "VARCHAR(128)"),
        ("smtp_port", "INTEGER DEFAULT 587"),
        ("smtp_user", "VARCHAR(128)"),
        ("smtp_password", "VARCHAR(256)"),
        ("smtp_from", "VARCHAR(128)"),
        ("smtp_security", "VARCHAR(16) DEFAULT 'starttls'"),
        ("allow_email_binding", "BOOLEAN DEFAULT 0"),
        ("allow_forgot_password", "BOOLEAN DEFAULT 0"),
        ("passkey_enabled", "BOOLEAN DEFAULT 0"),
        ("passkey_rp_ids", "TEXT DEFAULT '[]'"),
        ("passkey_allow_http", "BOOLEAN DEFAULT 0"),
        ("reminder_template", "TEXT"),
        ("render_enabled_help", "BOOLEAN DEFAULT 0"),
        ("render_enabled_list", "BOOLEAN DEFAULT 0"),
        ("render_enabled_progress", "BOOLEAN DEFAULT 0"),
        ("render_enabled_reminder", "BOOLEAN DEFAULT 0"),
        ("render_template", "VARCHAR(16) DEFAULT 'shadcn'"),
        ("render_font", "VARCHAR(128) DEFAULT ''"),
        ("render_font_dir", "VARCHAR(256) DEFAULT './data/fonts'"),
    ],
}


def _migrate_columns(conn) -> None:
    """为已存在的表补充新增列（SQLite 轻量迁移）"""
    for table, cols in COLUMN_MIGRATIONS.items():
        exists = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        existing = {
            row[1]
            for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        }
        for name, ddl in cols:
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


async def _ensure_admin_user(session) -> None:
    """把遗留 AuthSettings 中的管理员迁移为 role='admin' 的 User 行（统一用户系统）"""
    from .models import AuthSettings, OidcBinding, PasskeyCredential, User

    legacy = (
        await session.execute(select(AuthSettings).where(AuthSettings.id == 1))
    ).scalar_one_or_none()

    result = await session.execute(select(User).where(User.role == "admin"))
    admin = result.scalars().first()

    if not admin:
        admin = User(
            name=(legacy.admin_name if legacy else None) or "admin",
            role="admin",
            password_hash=legacy.admin_password_hash if legacy else None,
            avatar_path=legacy.admin_avatar_path if legacy else None,
            email=legacy.admin_email if legacy else None,
            email_verified=bool(legacy and legacy.admin_email_verified),
        )
        session.add(admin)
        await session.flush()
    elif legacy:
        changed = False
        if not admin.password_hash and legacy.admin_password_hash:
            admin.password_hash = legacy.admin_password_hash
            changed = True
        if not admin.avatar_path and legacy.admin_avatar_path:
            admin.avatar_path = legacy.admin_avatar_path
            changed = True
        if not admin.email and legacy.admin_email:
            admin.email = legacy.admin_email
            admin.email_verified = bool(legacy.admin_email_verified)
            changed = True
        if changed:
            await session.flush()

    # 迁移遗留管理员 Passkey / OIDC 绑定到 Admin 用户
    await session.execute(
        update(PasskeyCredential)
        .where(
            PasskeyCredential.is_admin == True,
            PasskeyCredential.user_id.is_(None),
        )
        .values(user_id=admin.id, is_admin=False)
    )
    await session.execute(
        update(OidcBinding)
        .where(
            OidcBinding.is_admin == True,
            OidcBinding.user_id.is_(None),
        )
        .values(user_id=admin.id, is_admin=False)
    )
    await session.flush()


def get_engine():
    return _engine


def get_session_factory():
    return _async_session_factory


async def init_db(db_path: str) -> None:
    """初始化数据库引擎，创建所有表"""
    global _engine, _async_session_factory

    # 确保数据目录存在
    db_dir = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(db_dir, exist_ok=True)

    db_url = f"sqlite+aiosqlite:///{db_path}"
    _engine = create_async_engine(db_url, echo=False)
    _async_session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    # 创建所有表
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_columns)

    # 统一用户系统：确保存在 role='admin' 的管理员账号（从遗留 AuthSettings 迁移）
    from .models import User
    from .webui.auth import hash_password
    import random
    import string
    from nonebot.log import logger

    async with get_session_factory()() as session:
        await _ensure_admin_user(session)
        await session.commit()
        admin = (
            await session.execute(select(User).where(User.role == "admin"))
        ).scalars().first()
        if admin and not admin.password_hash:
            # 首次运行生成 8 位随机密码
            initial_password = "".join(random.choices(string.ascii_letters + string.digits, k=8))
            admin.password_hash = hash_password(initial_password)
            await session.commit()

            # 使用高亮打印到控制台
            logger.opt(colors=True).info(
                f"<y>============== 代肝追踪 WebUI ==============</y>\n"
                f"<y>检测到初次启动，已生成初始管理员密码：</y><b><r>{initial_password}</r></b>\n"
                f"<y>请使用此密码登录 WebUI。登录后建议尽快修改密码。</y>\n"
                f"<y>============================================</y>"
            )


async def close_db() -> None:
    """关闭数据库连接"""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库会话（上下文管理器）"""
    if _async_session_factory is None:
        raise RuntimeError("数据库尚未初始化，请先调用 init_db()")
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            from nonebot.exception import FinishedException
            if isinstance(e, FinishedException):
                await session.commit()
            else:
                await session.rollback()
            raise
