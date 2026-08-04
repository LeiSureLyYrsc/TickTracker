"""定时提醒与当日备注命令"""
import re
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from sqlalchemy import delete, select

from ..database import get_session
from ..models import Commission, DailyNote, ReminderSetting, User
from ..webui.reminder import (
    DEFAULT_REMINDER_TEMPLATE,
    build_reminder_message,
    get_template,
    set_template,
)
from ..webui.render import maybe_render
from ..webui.utils import audit
from .admin import find_user

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULT_TIME = "22:00"


async def _user_has_commission(session, user_id: int) -> bool:
    result = await session.execute(
        select(Commission.id).where(Commission.user_id == user_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _get_or_create_setting(session, user_id: int) -> ReminderSetting:
    result = await session.execute(
        select(ReminderSetting).where(ReminderSetting.user_id == user_id)
    )
    rs = result.scalar_one_or_none()
    if not rs:
        rs = ReminderSetting(user_id=user_id, push_time=DEFAULT_TIME)
        session.add(rs)
        await session.flush()
    return rs


# ---- 用户自助：开启 / 关闭 / 状态 ----

cmd_reminder_on = on_command("代肝提醒开启", priority=5, block=True)


@cmd_reminder_on.handle()
async def handle_reminder_on(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒开启 [HH:MM] - 开启自己的定时提醒 """
    sender_qq = event.get_user_id()
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.qq_id == int(sender_qq)))
        ).scalar_one_or_none()
        if not user:
            await cmd_reminder_on.finish("您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号")
        if not await _user_has_commission(session, user.id):
            await cmd_reminder_on.finish("您还没有代肝数据，无法开启定时提醒")

        push_time = DEFAULT_TIME
        arg = args.extract_plain_text().strip()
        if arg:
            if not TIME_RE.match(arg):
                await cmd_reminder_on.finish("时间格式应为 HH:MM，例如 22:00")
            push_time = arg

        rs = await _get_or_create_setting(session, user.id)
        rs.push_time = push_time
        rs.enabled = True
        await audit(session, "qq", sender_qq, "开启定时提醒", target=f"用户「{user.name}」", detail=f"time={push_time}")
        await cmd_reminder_on.finish(f"已开启每日代肝提醒，推送时间：{push_time}")


cmd_reminder_off = on_command("代肝提醒关闭", priority=5, block=True)


@cmd_reminder_off.handle()
async def handle_reminder_off(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒关闭 - 关闭自己的定时提醒 """
    sender_qq = event.get_user_id()
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.qq_id == int(sender_qq)))
        ).scalar_one_or_none()
        if not user:
            await cmd_reminder_off.finish("您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号")
        rs = (
            await session.execute(select(ReminderSetting).where(ReminderSetting.user_id == user.id))
        ).scalar_one_or_none()
        if rs and rs.enabled:
            rs.enabled = False
            await audit(session, "qq", sender_qq, "关闭定时提醒", target=f"用户「{user.name}」")
            await cmd_reminder_off.finish("已关闭每日代肝提醒")
        await cmd_reminder_off.finish("您当前未开启定时提醒")


cmd_reminder_time = on_command("代肝提醒时间", priority=5, block=True)


@cmd_reminder_time.handle()
async def handle_reminder_time(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒时间 HH:MM - 修改自己的提醒时间（需已开启提醒） """
    push_time = args.extract_plain_text().strip()
    if not push_time:
        await cmd_reminder_time.finish("用法：/代肝提醒时间 HH:MM，例如 22:00")
    if not TIME_RE.match(push_time):
        await cmd_reminder_time.finish("时间格式应为 HH:MM，例如 22:00")

    sender_qq = event.get_user_id()
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.qq_id == int(sender_qq)))
        ).scalar_one_or_none()
        if not user:
            await cmd_reminder_time.finish("您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号")
        rs = (
            await session.execute(select(ReminderSetting).where(ReminderSetting.user_id == user.id))
        ).scalar_one_or_none()
        if not rs or not rs.enabled:
            await cmd_reminder_time.finish("请先开启提醒（/代肝提醒开启），再修改提醒时间")
        rs.push_time = push_time
        await audit(session, "qq", sender_qq, "修改提醒时间", target=f"用户「{user.name}」", detail=f"time={push_time}")
        await cmd_reminder_time.finish(f"已将提醒时间修改为 {push_time}")


# ---- 管理员：设置 / 移除 / 状态 / 模板 / 测试 ----

cmd_reminder_set = on_command("代肝提醒设置", permission=SUPERUSER, priority=5, block=True)


@cmd_reminder_set.handle()
async def handle_reminder_set(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒设置 用户名 HH:MM - 为用户开启定时提醒 """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_reminder_set.finish("用法：/代肝提醒设置 用户名 HH:MM")
    user_name, push_time = parts[0], parts[1]
    if not TIME_RE.match(push_time):
        await cmd_reminder_set.finish("时间格式应为 HH:MM，例如 22:00")

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_reminder_set.finish(f"未找到用户「{user_name}」")
        if not user.qq_id:
            await cmd_reminder_set.finish(f"用户「{user.name}」未绑定 QQ，无法推送")
        if not await _user_has_commission(session, user.id):
            await cmd_reminder_set.finish(f"用户「{user.name}」没有代肝数据，无法推送")

        rs = await _get_or_create_setting(session, user.id)
        rs.push_time = push_time
        rs.enabled = True
        await audit(session, "qq", event.get_user_id(), "设置定时提醒", target=f"用户「{user.name}」", detail=f"time={push_time}")
        await cmd_reminder_set.finish(f"已为用户「{user.name}」开启每日代肝提醒，推送时间：{push_time}")


cmd_reminder_remove = on_command("代肝提醒移除", permission=SUPERUSER, priority=5, block=True)


@cmd_reminder_remove.handle()
async def handle_reminder_remove(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒移除 用户名 - 移除用户的提醒设置 """
    user_name = args.extract_plain_text().strip()
    if not user_name:
        await cmd_reminder_remove.finish("用法：/代肝提醒移除 用户名")
    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_reminder_remove.finish(f"未找到用户「{user_name}」")
        rs = (
            await session.execute(select(ReminderSetting).where(ReminderSetting.user_id == user.id))
        ).scalar_one_or_none()
        if not rs:
            await cmd_reminder_remove.finish(f"用户「{user.name}」没有提醒设置")
        await session.delete(rs)
        await audit(session, "qq", event.get_user_id(), "移除定时提醒", target=f"用户「{user.name}」")
        await cmd_reminder_remove.finish(f"已移除用户「{user.name}」的提醒设置")


cmd_reminder_status = on_command("代肝提醒状态", priority=5, block=True)


@cmd_reminder_status.handle()
async def handle_reminder_status(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒状态 - 管理员查看全部，用户查看自己 """
    is_super = await SUPERUSER(bot, event)
    async with get_session() as session:
        if is_super:
            rows = (
                await session.execute(
                    select(ReminderSetting, User)
                    .join(User, ReminderSetting.user_id == User.id)
                    .order_by(User.name)
                )
            ).all()
            if not rows:
                await cmd_reminder_status.finish("暂无任何提醒设置")
            lines = ["=== 代肝提醒状态（全部）==="]
            for rs, u in rows:
                state = "开启" if rs.enabled else "关闭"
                lines.append(
                    f"[{u.name}] QQ:{u.qq_id or '未绑定'} | {state} | 时间 {rs.push_time} | "
                    f"今日已发:{'是' if rs.last_sent_date == datetime.now().strftime('%Y-%m-%d') else '否'}"
                )
            await cmd_reminder_status.finish("\n".join(lines))
        else:
            sender_qq = event.get_user_id()
            user = (
                await session.execute(select(User).where(User.qq_id == int(sender_qq)))
            ).scalar_one_or_none()
            if not user:
                await cmd_reminder_status.finish("您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号")
            rs = (
                await session.execute(select(ReminderSetting).where(ReminderSetting.user_id == user.id))
            ).scalar_one_or_none()
            if not rs:
                await cmd_reminder_status.finish("您尚未开启定时提醒，可使用 /代肝提醒开启 [HH:MM]")
            state = "开启" if rs.enabled else "关闭"
            await cmd_reminder_status.finish(
                f"您的代肝提醒：{state}\n推送时间：{rs.push_time}\n"
                f"今日已发送：{'是' if rs.last_sent_date == datetime.now().strftime('%Y-%m-%d') else '否'}"
            )


cmd_reminder_template = on_command("代肝提醒模板", permission=SUPERUSER, priority=5, block=True)


@cmd_reminder_template.handle()
async def handle_reminder_template(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒模板 内容 - 设置消息模板 """
    content = args.extract_plain_text().strip()
    if not content:
        await cmd_reminder_template.finish("用法：/代肝提醒模板 模板内容（支持 {name}{groups}{done}{total}{list}{note}）")
    async with get_session() as session:
        await set_template(session, content)
        await audit(session, "qq", event.get_user_id(), "设置提醒模板")
    await cmd_reminder_template.finish("提醒模板已更新")


cmd_reminder_template_reset = on_command("代肝提醒模板重置", permission=SUPERUSER, priority=5, block=True)


@cmd_reminder_template_reset.handle()
async def handle_reminder_template_reset(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝提醒模板重置 - 恢复默认模板 """
    async with get_session() as session:
        await set_template(session, DEFAULT_REMINDER_TEMPLATE)
        await audit(session, "qq", event.get_user_id(), "重置提醒模板")
    await cmd_reminder_template_reset.finish("已恢复默认模板")


cmd_reminder_test = on_command("测试代肝提醒推送", permission=SUPERUSER, priority=5, block=True)


@cmd_reminder_test.handle()
async def handle_reminder_test(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /测试代肝提醒推送 - 向自己发送预览推送 """
    async with get_session() as session:
        admin_user = (
            await session.execute(select(User).where(User.role == "admin").limit(1))
        ).scalars().first()
        template = await get_template(session)
        if admin_user:
            message = await build_reminder_message(session, admin_user)
        else:
            from ..webui.reminder import render_template

            message = render_template(
                template,
                {"name": "admin", "done": 0, "total": 0, "list": "", "note": "无备注"},
            )
        try:
            payload = await maybe_render(message, "reminder", session)
            await bot.send_private_msg(
                user_id=int(event.get_user_id()), message=payload or f"[预览]\n{message}"
            )
        except Exception as e:
            await cmd_reminder_test.finish(f"推送失败：{e}")
    await cmd_reminder_test.finish("已发送预览推送，请查收")


# ---- 当日备注（管理员） ----

cmd_note_set = on_command("代肝备注", permission=SUPERUSER, priority=5, block=True)


@cmd_note_set.handle()
async def handle_note_set(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝备注 用户名 内容 - 为用户添加当日备注 """
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await cmd_note_set.finish("用法：/代肝备注 用户名 备注内容")
    user_name, content = parts[0], parts[1].strip()
    if not content:
        await cmd_note_set.finish("备注内容不能为空")

    today = datetime.now().strftime("%Y-%m-%d")
    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_note_set.finish(f"未找到用户「{user_name}」")
        result = await session.execute(
            select(DailyNote).where(DailyNote.user_id == user.id, DailyNote.note_date == today)
        )
        note = result.scalar_one_or_none()
        if note:
            note.content = content
        else:
            session.add(DailyNote(user_id=user.id, note_date=today, content=content))
        await audit(session, "qq", event.get_user_id(), "添加当日备注", target=f"用户「{user.name}」", detail=content[:50])
    await cmd_note_set.finish(f"已为用户「{user.name}」添加今日备注「{content}」")


cmd_note_clear = on_command("代肝备注清除", permission=SUPERUSER, priority=5, block=True)


@cmd_note_clear.handle()
async def handle_note_clear(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝备注清除 用户名 - 清除用户当日备注 """
    user_name = args.extract_plain_text().strip()
    if not user_name:
        await cmd_note_clear.finish("用法：/代肝备注清除 用户名")
    today = datetime.now().strftime("%Y-%m-%d")
    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_note_clear.finish(f"未找到用户「{user_name}」")
        await session.execute(
            delete(DailyNote).where(
                DailyNote.user_id == user.id, DailyNote.note_date == today
            )
        )
        await audit(session, "qq", event.get_user_id(), "清除当日备注", target=f"用户「{user.name}」")
    await cmd_note_clear.finish(f"已清除用户「{user.name}」的今日备注")
