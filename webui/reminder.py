"""定时提醒消息模板与渲染"""
from datetime import datetime

from sqlalchemy import select

from ..models import Commission, DailyNote, Game, GameGroup, GroupCommission, SystemSettings

DEFAULT_REMINDER_TEMPLATE = (
    "[代肝通知推送]\n"
    "[用户: {name}]\n"
    "{groups}\n"
    "备注：{note}"
)


async def get_template(session) -> str:
    result = await session.execute(select(SystemSettings).where(SystemSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = SystemSettings(id=1)
        session.add(settings)
        await session.flush()
    return (settings.reminder_template or "").strip() or DEFAULT_REMINDER_TEMPLATE


async def set_template(session, template: str) -> str:
    result = await session.execute(select(SystemSettings).where(SystemSettings.id == 1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = SystemSettings(id=1)
        session.add(settings)
    settings.reminder_template = template.strip() or DEFAULT_REMINDER_TEMPLATE
    return settings.reminder_template


def reset_template_value() -> str:
    return DEFAULT_REMINDER_TEMPLATE


def render_template(template: str, data: dict) -> str:
    groups_text = data.get("groups") or "  （暂无代肝记录）"
    list_text = data.get("list") or ""
    return (
        template.replace("{name}", str(data.get("name", "")))
        .replace("{done}", str(data.get("done", 0)))
        .replace("{total}", str(data.get("total", 0)))
        .replace("{groups}", groups_text)
        .replace("{list}", list_text)
        .replace("{note}", str(data.get("note", "无备注")))
    )


async def _group_commissions(session, user):
    """按游戏组聚合某用户的代肝数据（与 QQ 命令一致的结构）"""
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

    order = sorted(k for k in by_group if k is not None)
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
            result.append(
                (groups_by_id.get(gid, "?"), gc_map.get(gid, 0), checked, len(items), completed, items)
            )
    return result


async def build_reminder_message(session, user) -> str:
    """按模板构建某用户的今日提醒消息"""
    template = await get_template(session)

    grouped = await _group_commissions(session, user)
    groups_lines: list[str] = []
    flat_lines: list[str] = []
    done = 0
    total = 0
    for name, total_due, checked, n, completed, items in grouped:
        if total_due is None:
            groups_lines.append(f"  组【{name}】打卡:{checked}/{n}")
        else:
            groups_lines.append(f"  组【{name}】应得:{total_due} | 打卡:{checked}/{n}")
        for comm, game in items:
            groups_lines.append(f"    {game.name}: {'✓ 已完成' if comm.checked_in else '✗ 未完成'}")
            flat_lines.append(
                f"{game.name} {'✓' if comm.checked_in else '✗'}（已完 {comm.completed_count}）"
            )
            if comm.checked_in:
                done += 1
            total += 1

    today = datetime.now().strftime("%Y-%m-%d")
    note_result = await session.execute(
        select(DailyNote).where(
            DailyNote.user_id == user.id, DailyNote.note_date == today
        )
    )
    note = note_result.scalar_one_or_none()

    return render_template(
        template,
        {
            "name": user.name,
            "done": done,
            "total": total,
            "groups": "\n".join(groups_lines),
            "list": "\n".join(flat_lines),
            "note": note.content if note else "无备注",
        },
    )
