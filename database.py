"""数据库引擎与会话工厂"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

_engine = None
_async_session_factory = None


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

    # 检查并初始化管理员密码
    from .models import AuthSettings
    from .webui.auth import hash_password
    import random
    import string
    from nonebot.log import logger

    async with get_session_factory()() as session:
        result = await session.execute(
            __import__('sqlalchemy').select(AuthSettings).where(AuthSettings.id == 1)
        )
        auth = result.scalar_one_or_none()
        if not auth:
            # 生成 8 位随机密码
            initial_password = "".join(random.choices(string.ascii_letters + string.digits, k=8))
            session.add(AuthSettings(id=1, admin_password_hash=hash_password(initial_password)))
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
