"""每日进度重置调度器"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from nonebot.log import logger
from sqlalchemy import update

from .database import get_session
from .models import Commission

_scheduler: AsyncIOScheduler | None = None


async def _reset_checkin_status() -> None:
    """重置所有用户的今日打卡状态为未完成"""
    logger.info("[代肝追踪] 执行每日打卡状态重置...")
    async with get_session() as session:
        await session.execute(
            update(Commission).values(checked_in=False)
        )
    logger.info("[代肝追踪] 打卡状态重置完成")


def start_scheduler(reset_hour: int = 4) -> None:
    """启动调度器"""
    global _scheduler

    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _reset_checkin_status,
        trigger=CronTrigger(hour=reset_hour, minute=0, second=0),
        id="daily_reset",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"[代肝追踪] 调度器已启动，每日 {reset_hour:02d}:00 重置打卡状态")


def stop_scheduler() -> None:
    """停止调度器"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[代肝追踪] 调度器已停止")
