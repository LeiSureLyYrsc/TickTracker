"""用户命令 handlers"""
import random
import string
from datetime import datetime, timedelta

from nonebot import get_bot, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from sqlalchemy import select

from ..database import get_session
from ..models import (
    Commission,
    Game,
    GameGroup,
    GroupCommission,
    LoginCode,
    Message as MsgModel,
    User,
)
from ..handlers.admin import find_game, find_user


async def _grouped_user_commissions(session, user):
    """按游戏组聚合某用户的代肝数据"""
    gc_rows = await session.execute(
        select(GroupCommission, GameGroup)
        .join(GameGroup, GroupCommission.game_group_id == GameGroup.id)
        .where(GroupCommission.user_id == user.id)
    )
    gc_map = {grp.id: gc.total_count for gc, grp in gc_rows.all()}

    comm_rows = await session.execute(
        select(Commission, Game)
        .join(Game, Commission.game_id == Game.id)
        .where(Commission.user_id == user.id)
        .order_by(Game.name)
    )
    by_group: dict[int | None, list] = {}
    for comm, game in comm_rows.all():
        by_group.setdefault(game.group_id, []).append((comm, game))

    groups = await session.execute(select(GameGroup).order_by(GameGroup.name))
    groups_by_id = {g.id: g.name for g in groups.scalars().all()}

    order = sorted((k for k in by_group if k is not None))
    if None in by_group:
        order.append(None)

    result = []
    for gid in order:
        items = by_group[gid]
        checked = sum(1 for c, _ in items if c.checked_in)
        completed = sum(c.completed_count for c, _ in items)
        if gid is None:
            result.append(("未分组", None, checked, len(items), completed, items))
        else:
            result.append((groups_by_id.get(gid, "?"), gc_map.get(gid, 0), checked, len(items), completed, items))
    return result


# ---- /代肝列表 ----

cmd_list = on_command("代肝列表", priority=5, block=True)


@cmd_list.handle()
async def handle_list(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    管理员：显示所有用户的代肝信息（详细表格式）
    普通用户：显示自己的代肝信息
    """
    is_super = await SUPERUSER(bot, event)

    async with get_session() as session:
        if is_super:
            # 管理员查看所有用户
            result = await session.execute(select(User).order_by(User.name))
            users = result.scalars().all()
            if not users:
                await cmd_list.finish("暂无用户数据")

            lines = ["=== 代肝列表（全部）==="]
            for user in users:
                qq_info = f"QQ:{user.qq_id}" if user.qq_id else "未绑定"
                lines.append(f"\n[用户: {user.name}] ({qq_info})")

                grouped = await _grouped_user_commissions(session, user)
                if grouped:
                    for name, total, checked, n, completed, items in grouped:
                        if total is None:
                            lines.append(f"  （{name}）")
                        else:
                            lines.append(f"  组【{name}】应得:{total} | 已完:{completed} | 打卡:{checked}/{n}")
                        for comm, game in items:
                            checked_s = "✓" if comm.checked_in else "✗"
                            lines.append(
                                f"    {game.name:<10} | 已完成:{comm.completed_count} | 今日打卡:{checked_s}"
                            )
                else:
                    lines.append("  （暂无代肝记录）")

            await cmd_list.finish("\n".join(lines))

        else:
            # 普通用户查自己
            sender_qq = event.get_user_id()
            result = await session.execute(
                select(User).where(User.qq_id == int(sender_qq))
            )
            user = result.scalar_one_or_none()
            if not user:
                await cmd_list.finish(
                    "您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号"
                )

            lines = [f"=== {user.name} 的代肝列表 ==="]
            grouped = await _grouped_user_commissions(session, user)
            if grouped:
                for name, total, checked, n, completed, items in grouped:
                    if total is None:
                        lines.append(f"  （{name}）")
                    else:
                        lines.append(f"  组【{name}】应得:{total} | 已完:{completed} | 打卡:{checked}/{n}")
                    for comm, game in items:
                        checked_s = "✓" if comm.checked_in else "✗"
                        lines.append(
                            f"    {game.name:<10} | 已完成:{comm.completed_count} | 今日打卡:{checked_s}"
                        )
            else:
                lines.append("  （暂无代肝记录）")

            await cmd_list.finish("\n".join(lines))


# ---- /进度查询 ----

cmd_progress = on_command("进度查询", priority=5, block=True)


@cmd_progress.handle()
async def handle_progress(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    管理员：显示所有用户今日进度
    普通用户：显示自己今日进度
    """
    is_super = await SUPERUSER(bot, event)

    async with get_session() as session:
        if is_super:
            result = await session.execute(select(User).order_by(User.name))
            users = result.scalars().all()
            if not users:
                await cmd_progress.finish("暂无用户数据")

            lines = ["=== 今日代肝进度（全部）==="]
            for user in users:
                grouped = await _grouped_user_commissions(session, user)
                if not grouped:
                    continue
                lines.append(f"\n[用户: {user.name}]")
                for name, total, checked, n, completed, items in grouped:
                    if total is None:
                        lines.append(f"  （{name}）")
                    else:
                        lines.append(f"  组【{name}】应得:{total} | 打卡:{checked}/{n}")
                    for comm, game in items:
                        checked_s = "✓ 已完成" if comm.checked_in else "✗ 未完成"
                        lines.append(f"    {game.name}: {checked_s}")

            await cmd_progress.finish("\n".join(lines))

        else:
            sender_qq = event.get_user_id()
            result = await session.execute(
                select(User).where(User.qq_id == int(sender_qq))
            )
            user = result.scalar_one_or_none()
            if not user:
                await cmd_progress.finish(
                    "您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号"
                )

            grouped = await _grouped_user_commissions(session, user)
            lines = [f"=== {user.name} 今日代肝进度 ==="]
            if grouped:
                for name, total, checked, n, completed, items in grouped:
                    if total is None:
                        lines.append(f"  （{name}）")
                    else:
                        lines.append(f"  组【{name}】应得:{total} | 打卡:{checked}/{n}")
                    for comm, game in items:
                        checked_s = "✓ 已完成" if comm.checked_in else "✗ 未完成"
                        lines.append(f"    {game.name}: {checked_s}")
            else:
                lines.append("  （暂无代肝记录）")

            await cmd_progress.finish("\n".join(lines))


# ---- /代肝留言 ----

cmd_message = on_command("代肝留言", priority=5, block=True)


@cmd_message.handle()
async def handle_message(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝留言 游戏名 信息内容
    """
    text = args.extract_plain_text().strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await cmd_message.finish("用法：/代肝留言 游戏名 信息内容")

    game_name, content = parts[0], parts[1]

    sender_qq = event.get_user_id()

    async with get_session() as session:
        # 查找发送者
        result = await session.execute(
            select(User).where(User.qq_id == int(sender_qq))
        )
        user = result.scalar_one_or_none()
        if not user:
            await cmd_message.finish(
                "您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号"
            )

        game = await find_game(session, game_name)
        if not game:
            await cmd_message.finish(f"未找到游戏「{game_name}」")

        # 保存留言
        msg = MsgModel(user_id=user.id, game_id=game.id, content=content)
        session.add(msg)
        await session.flush()

    # 向所有超级用户发送私聊消息
    from nonebot import get_driver
    driver = get_driver()
    superusers = driver.config.superusers

    notify_text = (
        f"[代肝留言]\n"
        f"用户：{user.name}（QQ:{sender_qq}）\n"
        f"游戏：{game.name}\n"
        f"内容：{content}"
    )

    for su_qq in superusers:
        try:
            await bot.send_private_msg(user_id=int(su_qq), message=notify_text)
        except Exception:
            pass

    await cmd_message.finish(f"留言已发送给管理员：「{content}」")


# ---- /代肝登录 ----

cmd_login = on_command("代肝登录", priority=5, block=True)


@cmd_login.handle()
async def handle_login(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝登录 - 获取6位数字验证码用于登录 WebUI
    """
    if event.message_type == "group":
        await cmd_login.finish("为了保护您的账号安全，请在私聊中使用 /代肝登录 命令。")

    from nonebot import get_plugin_config
    from ..config import Config
    config = get_plugin_config(Config)

    sender_qq = event.get_user_id()

    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.qq_id == int(sender_qq))
        )
        user = result.scalar_one_or_none()
        if not user:
            await cmd_login.finish(
                "您未绑定账号，请联系管理员使用 /代肝绑定 绑定您的QQ号"
            )

        # 生成6位数字验证码
        code = "".join(random.choices(string.digits, k=6))
        expires_at = datetime.now() + timedelta(seconds=config.commision_tracker_code_expire)

        # 使该用户之前的验证码失效
        old_codes = await session.execute(
            select(LoginCode).where(
                LoginCode.user_id == user.id,
                LoginCode.is_used == False,
            )
        )
        for old_code in old_codes.scalars().all():
            old_code.is_used = True

        # 保存新验证码
        login_code = LoginCode(
            user_id=user.id,
            code=code,
            expires_at=expires_at,
            is_used=False,
        )
        session.add(login_code)

    expire_min = config.commision_tracker_code_expire // 60
    server_url = config.commision_tracker_server_url

    await cmd_login.finish(
        f"您的登录验证码：{code}\n"
        f"有效期：{expire_min} 分钟\n"
        f"请访问：{server_url}\n"
        f"在登录页面输入验证码即可登录"
    )


# ---- /代肝帮助 ----

cmd_help = on_command("代肝帮助", priority=5, block=True)


@cmd_help.handle()
async def handle_help(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """根据发送者身份返回对应的帮助信息"""
    is_super = await SUPERUSER(bot, event)

    if is_super:
        msg = (
            "=== 代肝记录管理 · 管理员帮助 ===\n"
            "\n"
            "【数据管理】\n"
            "  /代肝创建 用户 名称\n"
            "    创建新用户\n"
            "  /代肝创建 游戏 名称 游戏组\n"
            "    创建新游戏并归入指定游戏组\n"
            "  /代肝别名添加 名称 别名\n"
            "    为用户或游戏添加别名\n"
            "  /代肝别名删除 名称 别名\n"
            "    删除用户或游戏的别名\n"
            "  /代肝添加游戏 用户名 游戏名\n"
            "    为用户添加某游戏的已完成跟踪记录\n"
            "  /代肝删除 用户名 游戏名\n"
            "    删除某游戏的已完成记录\n"
            "\n"
            "【游戏组管理】\n"
            "  /代肝游戏组创建 名称\n"
            "    创建游戏组\n"
            "  /代肝游戏组改名 旧名 新名\n"
            "    修改游戏组名称\n"
            "  /代肝游戏组删除 名称\n"
            "    删除游戏组（组内游戏变为未分组）\n"
            "  /代肝游戏移入 游戏名 游戏组\n"
            "    将游戏移动到指定游戏组\n"
            "\n"
            "【次数管理】（应得次数按游戏组记录）\n"
            "  /代肝添加 游戏组 用户名 次数\n"
            "    增加用户在游戏组的应得次数\n"
            "  /代肝次数设置 用户名 游戏组 次数/+n/-n\n"
            "    直接设置或增减游戏组应得次数\n"
            "  /代肝删除组 用户名 游戏组\n"
            "    删除用户在游戏组的应得记录\n"
            "  /代肝重置 用户名 游戏名\n"
            "    将某游戏已完成次数归零\n"
            "\n"
            "【打卡】\n"
            "  /代肝打卡 游戏名 用户名 [次数]\n"
            "    为用户打卡，默认 +1 次已完成\n"
            "\n"
            "【绑定】\n"
            "  /代肝绑定 用户名 QQ号/@用户\n"
            "    将用户与QQ号绑定\n"
            "  /代肝解绑 用户名\n"
            "    解除用户与QQ号的绑定\n"
            "\n"
            "【查询】\n"
            "  /代肝列表  查看所有用户代肝数据\n"
            "  /进度查询  查看所有用户今日进度\n"
            "\n"
            "【系统】\n"
            "  /代肝管理员密码设置 密码\n"
            "    设置 WebUI 管理员登录密码\n"
            "\n"
            "【通用】\n"
            "  /代肝留言 游戏名 内容  发留言给管理员\n"
            "  /代肝登录  获取 WebUI 登录验证码\n"
            "  /代肝帮助  显示此帮助"
        )
    else:
        msg = (
            "=== 代肝记录管理 · 用户帮助 ===\n"
            "\n"
            "【查询】\n"
            "  /代肝列表\n"
            "    查看自己的代肝信息\n"
            "    （按游戏组显示应得次数，按游戏显示已完成与今日打卡）\n"
            "  /进度查询\n"
            "    查看自己今日各游戏打卡状态\n"
            "\n"
            "【留言】\n"
            "  /代肝留言 游戏名 内容\n"
            "    向管理员发送留言\n"
            "    例：/代肝留言 原神 今天好像打了双份\n"
            "\n"
            "【WebUI 登录】\n"
            "  /代肝登录\n"
            "    获取6位数字验证码，用于登录 WebUI\n"
            "    在 WebUI 可查看数据、发送留言\n"
            "\n"
            "  /代肝帮助  显示此帮助\n"
            "\n"
            "如需绑定QQ号或添加代肝数据，\n"
            "请联系管理员操作。"
        )

    await cmd_help.finish(msg)
