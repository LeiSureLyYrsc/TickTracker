"""每日进度重置调度器 + 定时提醒"""
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from nonebot.log import logger
from sqlalchemy import delete, select, update

from .database import get_session
from .models import Commission, DailyNote, ReminderSetting, User
from .webui.reminder import build_reminder_message

_scheduler: AsyncIOScheduler | None = None


async def _reset_checkin_status() -> None:
    """重置所有用户的今日打卡状态，并清除当日备注"""
    logger.info("[代肝追踪] 执行每日打卡状态重置...")
    today = datetime.now().strftime("%Y-%m-%d")
    async with get_session() as session:
        await session.execute(update(Commission).values(checked_in=False))
        await session.execute(delete(DailyNote).where(DailyNote.note_date == today))
    logger.info("[代肝追踪] 打卡状态与当日备注已重置")


async def _send_reminders() -> None:
    """按每个用户的推送时间发送每日代肝提醒"""
    now = datetime.now()
    hhmm = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")

    try:
        import nonebot

        bot = nonebot.get_bot()
    except Exception:
        return

    # 先收集目标用户
    targets: list[tuple] = []
    async with get_session() as session:
        rows = (
            await session.execute(
                select(ReminderSetting, User)
                .join(User, ReminderSetting.user_id == User.id)
                .where(
                    ReminderSetting.enabled == True,
                    ReminderSetting.push_time == hhmm,
                )
            )
        ).all()
        for rs, user in rows:
            if not user.qq_id or rs.last_sent_date == today:
                continue
            targets.append((rs.id, user.id, user.qq_id))

    if not targets:
        return

    # 逐个渲染并发送
    for rs_id, user_id, qq_id in targets:
        try:
            async with get_session() as session:
                user = (
                    await session.execute(select(User).where(User.id == user_id))
                ).scalar_one()
                message = await build_reminder_message(session, user)
                rs = (
                    await session.execute(
                        select(ReminderSetting).where(ReminderSetting.id == rs_id)
                    )
                ).scalar_one()
                await bot.send_private_msg(user_id=qq_id, message=message)
                rs.last_sent_date = today
        except Exception as e:
            logger.warning(f"[代肝追踪] 提醒推送失败 user_id={user_id}: {e}")


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
    _scheduler.add_job(
        _send_reminders,
        trigger=CronTrigger(minute="*"),
        id="reminder_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        f"[代肝追踪] 调度器已启动，每日 {reset_hour:02d}:00 重置打卡状态，每分钟检查定时提醒"
    )


def stop_scheduler() -> None:
    """停止调度器"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[代肝追踪] 调度器已停止")
