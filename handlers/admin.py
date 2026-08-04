"""管理员命令 handlers"""
import re
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from sqlalchemy import delete, select, update

from ..database import get_session
from ..models import (
    Commission,
    Game,
    GameAlias,
    GameGroup,
    GroupCommission,
    User,
    UserAlias,
)
from ..webui.auth import hash_password
from ..webui.utils import audit

# ---- 权限 ----
ADMIN_PERM = SUPERUSER


# ---- 工具函数 ----

async def find_user(session, name: str) -> User | None:
    """按名称或别名查找用户"""
    result = await session.execute(select(User).where(User.name == name))
    user = result.scalar_one_or_none()
    if user:
        return user
    result = await session.execute(
        select(User).join(UserAlias).where(UserAlias.alias == name)
    )
    return result.scalar_one_or_none()


async def find_game(session, name: str) -> Game | None:
    """按名称或别名查找游戏"""
    result = await session.execute(select(Game).where(Game.name == name))
    game = result.scalar_one_or_none()
    if game:
        return game
    result = await session.execute(
        select(Game).join(GameAlias).where(GameAlias.alias == name)
    )
    return result.scalar_one_or_none()


async def get_or_create_commission(session, user_id: int, game_id: int) -> Commission:
    """获取或创建代肝记录"""
    result = await session.execute(
        select(Commission).where(
            Commission.user_id == user_id, Commission.game_id == game_id
        )
    )
    comm = result.scalar_one_or_none()
    if not comm:
        comm = Commission(user_id=user_id, game_id=game_id)
        session.add(comm)
        await session.flush()
    return comm


async def find_group(session, name: str) -> GameGroup | None:
    """按名称查找游戏组"""
    result = await session.execute(select(GameGroup).where(GameGroup.name == name))
    return result.scalar_one_or_none()


async def resolve_group(session, name: str) -> GameGroup | None:
    """按名称解析游戏组：优先游戏组名，其次游戏名所属的游戏组"""
    grp = await find_group(session, name)
    if grp:
        return grp
    game = await find_game(session, name)
    if game and game.group:
        return game.group
    return None


async def get_or_create_group_commission(
    session, user_id: int, game_group_id: int
) -> GroupCommission:
    """获取或创建游戏组应得记录"""
    result = await session.execute(
        select(GroupCommission).where(
            GroupCommission.user_id == user_id,
            GroupCommission.game_group_id == game_group_id,
        )
    )
    gc = result.scalar_one_or_none()
    if not gc:
        gc = GroupCommission(user_id=user_id, game_group_id=game_group_id)
        session.add(gc)
        await session.flush()
    return gc


# ---- /代肝创建 ----

cmd_create = on_command("代肝创建", permission=ADMIN_PERM, priority=5, block=True)


@cmd_create.handle()
async def handle_create(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝创建 用户 用户名
    /代肝创建 游戏 游戏名 游戏组
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_create.finish("用法：/代肝创建 用户 名称 或 /代肝创建 游戏 名称 游戏组")

    entity_type, name = parts[0], parts[1]

    async with get_session() as session:
        if entity_type == "用户":
            existing = await session.execute(select(User).where(User.name == name))
            if existing.scalar_one_or_none():
                await cmd_create.finish(f"用户「{name}」已存在")
            session.add(User(name=name))
            await audit(session, "qq", event.get_user_id(), "创建用户", target=f"用户「{name}」")
            await cmd_create.finish(f"已创建用户「{name}」")

        elif entity_type == "游戏":
            if len(parts) < 3:
                await cmd_create.finish("用法：/代肝创建 游戏 游戏名 游戏组")
            group_name = parts[2]
            existing = await session.execute(select(Game).where(Game.name == name))
            if existing.scalar_one_or_none():
                await cmd_create.finish(f"游戏「{name}」已存在")
            grp = await find_group(session, group_name)
            if not grp:
                await cmd_create.finish(f"未找到游戏组「{group_name}」")
            session.add(Game(name=name, group_id=grp.id))
            await audit(session, "qq", event.get_user_id(), "创建游戏", target=f"游戏「{name}」", detail=f"游戏组「{grp.name}」")
            await cmd_create.finish(f"已创建游戏「{name}」并归入游戏组「{grp.name}」")

        else:
            await cmd_create.finish("第一个参数必须是「用户」或「游戏」")


# ---- 游戏组管理 ----

cmd_group_create = on_command("代肝游戏组创建", permission=ADMIN_PERM, priority=5, block=True)


@cmd_group_create.handle()
async def handle_group_create(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝游戏组创建 名称 """
    name = args.extract_plain_text().strip()
    if not name:
        await cmd_group_create.finish("用法：/代肝游戏组创建 名称")

    async with get_session() as session:
        existing = await session.execute(select(GameGroup).where(GameGroup.name == name))
        if existing.scalar_one_or_none():
            await cmd_group_create.finish(f"游戏组「{name}」已存在")
        session.add(GameGroup(name=name))
        await audit(session, "qq", event.get_user_id(), "创建游戏组", target=f"游戏组「{name}」")
        await cmd_group_create.finish(f"已创建游戏组「{name}」")


cmd_group_rename = on_command("代肝游戏组改名", permission=ADMIN_PERM, priority=5, block=True)


@cmd_group_rename.handle()
async def handle_group_rename(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝游戏组改名 旧名 新名 """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_group_rename.finish("用法：/代肝游戏组改名 旧名 新名")

    old_name, new_name = parts[0], parts[1]

    async with get_session() as session:
        grp = await find_group(session, old_name)
        if not grp:
            await cmd_group_rename.finish(f"未找到游戏组「{old_name}」")
        existing = await session.execute(select(GameGroup).where(GameGroup.name == new_name))
        if existing.scalar_one_or_none():
            await cmd_group_rename.finish(f"游戏组「{new_name}」已存在")
        grp.name = new_name
        await audit(session, "qq", event.get_user_id(), "修改游戏组名", target=f"游戏组「{old_name}」", detail=new_name)
        await cmd_group_rename.finish(f"游戏组已改名为「{new_name}」")


cmd_group_delete = on_command("代肝游戏组删除", permission=ADMIN_PERM, priority=5, block=True)


@cmd_group_delete.handle()
async def handle_group_delete(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝游戏组删除 名称（组内游戏变为未分组） """
    name = args.extract_plain_text().strip()
    if not name:
        await cmd_group_delete.finish("用法：/代肝游戏组删除 名称")

    async with get_session() as session:
        grp = await find_group(session, name)
        if not grp:
            await cmd_group_delete.finish(f"未找到游戏组「{name}」")
        await session.execute(update(Game).where(Game.group_id == grp.id).values(group_id=None))
        await audit(session, "qq", event.get_user_id(), "删除游戏组", target=f"游戏组「{name}」")
        await session.delete(grp)
        await cmd_group_delete.finish(f"游戏组「{name}」已删除，组内游戏变为未分组")


cmd_game_move = on_command("代肝游戏移入", permission=ADMIN_PERM, priority=5, block=True)


@cmd_game_move.handle()
async def handle_game_move(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """ /代肝游戏移入 游戏名 游戏组 """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_game_move.finish("用法：/代肝游戏移入 游戏名 游戏组")

    game_name, group_name = parts[0], parts[1]

    async with get_session() as session:
        game = await find_game(session, game_name)
        if not game:
            await cmd_game_move.finish(f"未找到游戏「{game_name}」")
        grp = await find_group(session, group_name)
        if not grp:
            await cmd_game_move.finish(f"未找到游戏组「{group_name}」")
        game.group_id = grp.id
        await audit(session, "qq", event.get_user_id(), "移动游戏", target=f"游戏「{game.name}」", detail=f"游戏组「{grp.name}」")
        await cmd_game_move.finish(f"已将游戏「{game.name}」移入游戏组「{grp.name}」")


# ---- /代肝别名添加 ----

cmd_alias_add = on_command("代肝别名添加", permission=ADMIN_PERM, priority=5, block=True)


@cmd_alias_add.handle()
async def handle_alias_add(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝别名添加 用户 别名
    /代肝别名添加 游戏 别名
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_alias_add.finish("用法：/代肝别名添加 用户名/游戏名 别名")

    target_name, alias = parts[0], parts[1]

    async with get_session() as session:
        # 先尝试查用户
        user = await find_user(session, target_name)
        if user:
            existing = await session.execute(
                select(UserAlias).where(UserAlias.alias == alias)
            )
            if existing.scalar_one_or_none():
                await cmd_alias_add.finish(f"别名「{alias}」已被占用")
            session.add(UserAlias(user_id=user.id, alias=alias))
            await audit(session, "qq", event.get_user_id(), "添加用户别名", target=f"用户「{user.name}」", detail=f"别名「{alias}」")
            await cmd_alias_add.finish(f"已为用户「{user.name}」添加别名「{alias}」")

        # 再尝试查游戏
        game = await find_game(session, target_name)
        if game:
            existing = await session.execute(
                select(GameAlias).where(GameAlias.alias == alias)
            )
            if existing.scalar_one_or_none():
                await cmd_alias_add.finish(f"别名「{alias}」已被占用")
            session.add(GameAlias(game_id=game.id, alias=alias))
            await audit(session, "qq", event.get_user_id(), "添加游戏别名", target=f"游戏「{game.name}」", detail=f"别名「{alias}」")
            await cmd_alias_add.finish(f"已为游戏「{game.name}」添加别名「{alias}」")

        await cmd_alias_add.finish(f"未找到用户或游戏「{target_name}」")


# ---- /代肝别名删除 ----

cmd_alias_del = on_command("代肝别名删除", permission=ADMIN_PERM, priority=5, block=True)


@cmd_alias_del.handle()
async def handle_alias_del(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝别名删除 用户名/游戏名 别名
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_alias_del.finish("用法：/代肝别名删除 用户名/游戏名 别名")

    target_name, alias = parts[0], parts[1]

    async with get_session() as session:
        # 先尝试删除用户别名
        user = await find_user(session, target_name)
        if user:
            result = await session.execute(
                select(UserAlias).where(
                    UserAlias.user_id == user.id, UserAlias.alias == alias
                )
            )
            ua = result.scalar_one_or_none()
            if ua:
                await session.delete(ua)
                await audit(session, "qq", event.get_user_id(), "删除用户别名", target=f"用户「{user.name}」", detail=f"别名「{alias}」")
                await cmd_alias_del.finish(f"已删除用户「{user.name}」的别名「{alias}」")
            else:
                await cmd_alias_del.finish(f"用户「{user.name}」没有别名「{alias}」")

        # 再尝试删除游戏别名
        game = await find_game(session, target_name)
        if game:
            result = await session.execute(
                select(GameAlias).where(
                    GameAlias.game_id == game.id, GameAlias.alias == alias
                )
            )
            ga = result.scalar_one_or_none()
            if ga:
                await session.delete(ga)
                await audit(session, "qq", event.get_user_id(), "删除游戏别名", target=f"游戏「{game.name}」", detail=f"别名「{alias}」")
                await cmd_alias_del.finish(f"已删除游戏「{game.name}」的别名「{alias}」")
            else:
                await cmd_alias_del.finish(f"游戏「{game.name}」没有别名「{alias}」")

        await cmd_alias_del.finish(f"未找到用户或游戏「{target_name}」")


# ---- /代肝添加 ----

cmd_add = on_command("代肝添加", permission=ADMIN_PERM, priority=5, block=True)


@cmd_add.handle()
async def handle_add(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝添加 游戏组 用户名 次数
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 3:
        await cmd_add.finish("用法：/代肝添加 游戏组 用户名 次数")

    group_name, user_name, count_str = parts[0], parts[1], parts[2]
    if not count_str.lstrip("-").isdigit():
        await cmd_add.finish("次数必须是整数")
    count = int(count_str)

    async with get_session() as session:
        grp = await resolve_group(session, group_name)
        if not grp:
            await cmd_add.finish(f"未找到游戏组「{group_name}」")
        user = await find_user(session, user_name)
        if not user:
            await cmd_add.finish(f"未找到用户「{user_name}」")

        gc = await get_or_create_group_commission(session, user.id, grp.id)
        gc.total_count += count
        await audit(session, "qq", event.get_user_id(), "添加应得次数", target=f"用户「{user.name}」- 游戏组「{grp.name}」", detail=f"+{count} total={gc.total_count}")
        await cmd_add.finish(
            f"已为用户「{user.name}」的游戏组「{grp.name}」添加 {count} 次应得次数\n"
            f"当前应得次数：{gc.total_count}"
        )


# ---- /代肝添加游戏 ----

cmd_add_game = on_command("代肝添加游戏", permission=ADMIN_PERM, priority=5, block=True)


@cmd_add_game.handle()
async def handle_add_game(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝添加游戏 用户名 游戏名
    为用户的某个游戏添加已完成跟踪记录（应得次数由游戏组记录）
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_add_game.finish("用法：/代肝添加游戏 用户名 游戏名")

    user_name, game_name = parts[0], parts[1]

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_add_game.finish(f"未找到用户「{user_name}」")
        game = await find_game(session, game_name)
        if not game:
            await cmd_add_game.finish(f"未找到游戏「{game_name}」")

        comm = await get_or_create_commission(session, user.id, game.id)
        await audit(session, "qq", event.get_user_id(), "添加代肝记录", target=f"用户「{user.name}」- 游戏「{game.name}」")
        await cmd_add_game.finish(
            f"已为用户「{user.name}」添加游戏「{game.name}」的已完成跟踪记录"
        )


# ---- /代肝删除组 ----

cmd_delete_group = on_command("代肝删除组", permission=ADMIN_PERM, priority=5, block=True)


@cmd_delete_group.handle()
async def handle_delete_group(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝删除组 用户名 游戏组
    删除该用户在指定游戏组下的应得记录
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_delete_group.finish("用法：/代肝删除组 用户名 游戏组")

    user_name, group_name = parts[0], parts[1]

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_delete_group.finish(f"未找到用户「{user_name}」")
        grp = await resolve_group(session, group_name)
        if not grp:
            await cmd_delete_group.finish(f"未找到游戏组「{group_name}」")

        result = await session.execute(
            select(GroupCommission).where(
                GroupCommission.user_id == user.id,
                GroupCommission.game_group_id == grp.id,
            )
        )
        gc = result.scalar_one_or_none()
        if not gc:
            await cmd_delete_group.finish(
                f"用户「{user.name}」在游戏组「{grp.name}」下没有应得记录"
            )
        await audit(session, "qq", event.get_user_id(), "删除应得记录", target=f"用户「{user.name}」- 游戏组「{grp.name}」", detail=f"total={gc.total_count}")
        await session.delete(gc)
        await cmd_delete_group.finish(
            f"已删除用户「{user.name}」在游戏组「{grp.name}」下的应得记录"
        )


# ---- /代肝绑定 ----

cmd_bind = on_command("代肝绑定", permission=ADMIN_PERM, priority=5, block=True)


@cmd_bind.handle()
async def handle_bind(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝绑定 用户名 QQ号
    /代肝绑定 用户名 @用户
    """
    text = args.extract_plain_text().strip()
    # 解析 @ 消息段
    at_qq = None
    for seg in args:
        if seg.type == "at":
            at_qq = int(seg.data["qq"])
            break

    parts = text.split()
    if len(parts) < 1:
        await cmd_bind.finish("用法：/代肝绑定 用户名 QQ号 或 /代肝绑定 用户名 @用户")

    user_name = parts[0]
    if at_qq is None:
        if len(parts) < 2:
            await cmd_bind.finish("用法：/代肝绑定 用户名 QQ号 或 /代肝绑定 用户名 @用户")
        qq_str = parts[1]
        if not qq_str.isdigit():
            await cmd_bind.finish("QQ号必须是纯数字")
        at_qq = int(qq_str)

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_bind.finish(f"未找到用户「{user_name}」")

        # 检查 QQ 号是否已被其他用户绑定
        existing = await session.execute(
            select(User).where(User.qq_id == at_qq)
        )
        other = existing.scalar_one_or_none()
        if other and other.id != user.id:
            await cmd_bind.finish(f"QQ {at_qq} 已绑定到用户「{other.name}」")

        user.qq_id = at_qq
        await audit(session, "qq", event.get_user_id(), "绑定QQ", target=f"用户「{user.name}」", detail=f"qq_id={at_qq}")
        await cmd_bind.finish(f"已将用户「{user.name}」绑定到 QQ {at_qq}")


# ---- /代肝打卡 ----

cmd_checkin = on_command("代肝打卡", permission=ADMIN_PERM, priority=5, block=True)


@cmd_checkin.handle()
async def handle_checkin(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝打卡 游戏名 用户名 [次数]
    无次数参数则 +1
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_checkin.finish("用法：/代肝打卡 游戏名 用户名 [次数]")

    game_name, user_name = parts[0], parts[1]
    count = 1
    if len(parts) >= 3:
        if not parts[2].isdigit():
            await cmd_checkin.finish("次数必须是正整数")
        count = int(parts[2])
        if count <= 0:
            await cmd_checkin.finish("次数必须大于0")

    async with get_session() as session:
        game = await find_game(session, game_name)
        if not game:
            await cmd_checkin.finish(f"未找到游戏「{game_name}」")
        user = await find_user(session, user_name)
        if not user:
            await cmd_checkin.finish(f"未找到用户「{user_name}」")

        comm = await get_or_create_commission(session, user.id, game.id)
        comm.completed_count += count
        comm.checked_in = True
        comm.last_checked_in_at = datetime.now()
        await audit(session, "qq", event.get_user_id(), "打卡", target=f"用户「{user.name}」- 游戏「{game.name}」", detail=f"+{count} completed={comm.completed_count}")
        await cmd_checkin.finish(
            f"已为用户「{user.name}」的游戏「{game.name}」打卡 +{count}\n"
            f"已完成次数：{comm.completed_count}，今日打卡：✓"
        )


# ---- /代肝解绑 ----

cmd_unbind = on_command("代肝解绑", permission=ADMIN_PERM, priority=5, block=True)


@cmd_unbind.handle()
async def handle_unbind(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝解绑 用户名
    """
    user_name = args.extract_plain_text().strip()
    if not user_name:
        await cmd_unbind.finish("用法：/代肝解绑 用户名")

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_unbind.finish(f"未找到用户「{user_name}」")
        if user.qq_id is None:
            await cmd_unbind.finish(f"用户「{user.name}」未绑定QQ号")
        old_qq = user.qq_id
        user.qq_id = None
        await audit(session, "qq", event.get_user_id(), "解绑QQ", target=f"用户「{user.name}」", detail=f"old_qq={old_qq}")
        await cmd_unbind.finish(f"已解除用户「{user.name}」与 QQ {old_qq} 的绑定")


# ---- /代肝次数设置 ----

cmd_set_count = on_command("代肝次数设置", permission=ADMIN_PERM, priority=5, block=True)


@cmd_set_count.handle()
async def handle_set_count(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝次数设置 用户名 游戏组 次数
    /代肝次数设置 用户名 游戏组 +10
    /代肝次数设置 用户名 游戏组 -10
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 3:
        await cmd_set_count.finish("用法：/代肝次数设置 用户名 游戏组 次数/+n/-n")

    user_name, group_name, count_str = parts[0], parts[1], parts[2]

    # 解析次数：支持 +n, -n, n
    relative = False
    if count_str.startswith("+"):
        relative = True
        delta = int(count_str[1:])
    elif count_str.startswith("-"):
        relative = True
        delta = -int(count_str[1:])
    elif count_str.lstrip("-").isdigit():
        delta = int(count_str)
    else:
        await cmd_set_count.finish("次数格式不正确，支持：整数、+n、-n")
        return

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_set_count.finish(f"未找到用户「{user_name}」")
        grp = await resolve_group(session, group_name)
        if not grp:
            await cmd_set_count.finish(f"未找到游戏组「{group_name}」")

        gc = await get_or_create_group_commission(session, user.id, grp.id)
        if relative:
            gc.total_count = max(0, gc.total_count + delta)
            action = f"已{'增加' if delta >= 0 else '减少'} {abs(delta)} 次"
        else:
            gc.total_count = max(0, delta)
            action = f"已设置为 {gc.total_count} 次"
        await audit(session, "qq", event.get_user_id(), "设置应得次数", target=f"用户「{user.name}」- 游戏组「{grp.name}」", detail=f"delta={delta} total={gc.total_count}")
        await cmd_set_count.finish(
            f"用户「{user.name}」游戏组「{grp.name}」应得次数{action}\n"
            f"当前应得次数：{gc.total_count}"
        )


# ---- /代肝重置 ----

cmd_reset = on_command("代肝重置", permission=ADMIN_PERM, priority=5, block=True)


@cmd_reset.handle()
async def handle_reset(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝重置 用户名 游戏名
    让已代肝次数归零
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_reset.finish("用法：/代肝重置 用户名 游戏名")

    user_name, game_name = parts[0], parts[1]

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_reset.finish(f"未找到用户「{user_name}」")
        game = await find_game(session, game_name)
        if not game:
            await cmd_reset.finish(f"未找到游戏「{game_name}」")

        comm = await get_or_create_commission(session, user.id, game.id)
        comm.completed_count = 0
        comm.checked_in = False
        await audit(session, "qq", event.get_user_id(), "重置已完成", target=f"用户「{user.name}」- 游戏「{game.name}」")
        await cmd_reset.finish(
            f"已重置用户「{user.name}」游戏「{game.name}」的已完成次数（归零）"
        )


# ---- /代肝删除 ----

cmd_delete = on_command("代肝删除", permission=ADMIN_PERM, priority=5, block=True)


@cmd_delete.handle()
async def handle_delete(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    /代肝删除 用户名 游戏名
    """
    parts = args.extract_plain_text().strip().split()
    if len(parts) < 2:
        await cmd_delete.finish("用法：/代肝删除 用户名 游戏名")

    user_name, game_name = parts[0], parts[1]

    async with get_session() as session:
        user = await find_user(session, user_name)
        if not user:
            await cmd_delete.finish(f"未找到用户「{user_name}」")
        game = await find_game(session, game_name)
        if not game:
            await cmd_delete.finish(f"未找到游戏「{game_name}」")

        result = await session.execute(
            select(Commission).where(
                Commission.user_id == user.id, Commission.game_id == game.id
            )
        )
        comm = result.scalar_one_or_none()
        if not comm:
            await cmd_delete.finish(
                f"用户「{user.name}」没有游戏「{game.name}」的代肝记录"
            )
        await audit(session, "qq", event.get_user_id(), "删除代肝记录", target=f"用户「{user.name}」- 游戏「{game.name}」")
        await session.delete(comm)
        await cmd_delete.finish(
            f"已删除用户「{user.name}」游戏「{game.name}」的代肝数据"
        )


# ---- /代肝管理员密码设置 ----

cmd_set_password = on_command(
    "代肝管理员密码设置", permission=ADMIN_PERM, priority=5, block=True
)


@cmd_set_password.handle()
async def handle_set_password(
    bot: Bot, event: MessageEvent, args: Message = CommandArg()
):
    """
    /代肝管理员密码设置 密码
    """
    password = args.extract_plain_text().strip()
    if not password:
        await cmd_set_password.finish("用法：/代肝管理员密码设置 密码")
    if len(password) < 6:
        await cmd_set_password.finish("密码长度至少6位")

    async with get_session() as session:
        result = await session.execute(select(User).where(User.role == "admin").limit(1))
        admin = result.scalars().first()
        if admin:
            admin.password_hash = hash_password(password)
        await audit(session, "qq", event.get_user_id(), "设置管理员密码")
        await cmd_set_password.finish("管理员密码已更新")
