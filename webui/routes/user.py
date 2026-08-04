"""用户 API 路由"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...database import get_session
from ...models import (
    Commission,
    DailyNote,
    Game,
    GameGroup,
    GroupCommission,
    Message,
    ReminderSetting,
    User,
)
from ..routes.auth import require_user
from ..utils import audit, get_client_ip
from .reminders import TIME_RE

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me/commissions")
async def get_my_commissions(payload: dict = Depends(require_user)):
    """获取当前用户的代肝记录"""
    user_id = int(payload.get("sub"))

    async with get_session() as session:
        result = await session.execute(
            select(Commission, Game)
            .join(Game, Commission.game_id == Game.id)
            .where(Commission.user_id == user_id)
            .options(selectinload(Game.group))
            .order_by(Game.name)
        )
        records = result.all()
        return [
            {
                "id": c.id,
                "game_id": c.game_id,
                "game_name": g.name,
                "group_id": g.group_id,
                "group_name": g.group.name if g.group else None,
                "completed_count": c.completed_count,
                "checked_in": c.checked_in,
                "last_checked_in_at": (
                    c.last_checked_in_at.isoformat() if c.last_checked_in_at else None
                ),
            }
            for c, g in records
        ]


@router.get("/me/group-commissions")
async def get_my_group_commissions(payload: dict = Depends(require_user)):
    """获取当前用户在游戏组下的应得次数"""
    user_id = int(payload.get("sub"))

    async with get_session() as session:
        result = await session.execute(
            select(GroupCommission, GameGroup)
            .join(GameGroup, GroupCommission.game_group_id == GameGroup.id)
            .where(GroupCommission.user_id == user_id)
            .order_by(GameGroup.name)
        )
        records = result.all()
        return [
            {
                "game_group_id": g.id,
                "group_name": g.name,
                "total_count": gc.total_count,
            }
            for gc, g in records
        ]


@router.get("/me/progress")
async def get_my_progress(payload: dict = Depends(require_user)):
    """获取当前用户今日打卡进度"""
    user_id = int(payload.get("sub"))

    async with get_session() as session:
        result = await session.execute(
            select(Commission, Game)
            .join(Game, Commission.game_id == Game.id)
            .where(Commission.user_id == user_id)
            .options(selectinload(Game.group))
            .order_by(Game.name)
        )
        records = result.all()
        return [
            {
                "game_name": g.name,
                "group_id": g.group_id,
                "group_name": g.group.name if g.group else None,
                "checked_in": c.checked_in,
                "last_checked_in_at": (
                    c.last_checked_in_at.isoformat() if c.last_checked_in_at else None
                ),
            }
            for c, g in records
        ]


@router.get("/me/messages")
async def get_my_messages(payload: dict = Depends(require_user)):
    """获取当前用户的历史留言（含已读状态）"""
    user_id = int(payload.get("sub"))

    async with get_session() as session:
        result = await session.execute(
            select(Message, Game)
            .join(Game, Message.game_id == Game.id)
            .where(Message.user_id == user_id)
            .order_by(Message.created_at.desc())
        )
        records = result.all()
        return [
            {
                "id": m.id,
                "game_name": g.name,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "is_read": m.is_read,
            }
            for m, g in records
        ]


@router.get("/me/note")
async def get_my_note(payload: dict = Depends(require_user)):
    """获取当前用户当日备注"""
    user_id = int(payload.get("sub"))
    today = datetime.now().strftime("%Y-%m-%d")
    async with get_session() as session:
        result = await session.execute(
            select(DailyNote).where(
                DailyNote.user_id == user_id, DailyNote.note_date == today
            )
        )
        note = result.scalar_one_or_none()
        return {"content": note.content if note else ""}


class ReminderRequest(BaseModel):
    enabled: bool | None = None
    push_time: str | None = None


@router.get("/me/reminder")
async def get_my_reminder(payload: dict = Depends(require_user)):
    """获取当前用户的定时提醒设置"""
    user_id = int(payload.get("sub"))
    async with get_session() as session:
        result = await session.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        )
        rs = result.scalar_one_or_none()
        if not rs:
            return {"enabled": False, "push_time": "22:00", "last_sent_date": None}
        return {
            "enabled": rs.enabled,
            "push_time": rs.push_time,
            "last_sent_date": rs.last_sent_date,
        }


@router.put("/me/reminder")
async def update_my_reminder(
    body: ReminderRequest, request: Request, payload: dict = Depends(require_user)
):
    """启用/关闭提醒或修改推送时间"""
    user_id = int(payload.get("sub"))
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        result = await session.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        )
        rs = result.scalar_one_or_none()

        if body.push_time is not None and not TIME_RE.match(body.push_time):
            raise HTTPException(status_code=400, detail="推送时间格式应为 HH:MM（如 22:00）")

        enabled = body.enabled if body.enabled is not None else (rs.enabled if rs else False)
        push_time = body.push_time if body.push_time is not None else (rs.push_time if rs else "22:00")

        # 启用时需要资格：绑定 QQ + 有代肝数据
        if enabled and not (rs and rs.enabled):
            if not user.qq_id:
                raise HTTPException(status_code=400, detail="您未绑定 QQ，无法启用提醒")
            has = await session.execute(
                select(Commission.id).where(Commission.user_id == user.id).limit(1)
            )
            if has.scalar_one_or_none() is None:
                raise HTTPException(status_code=400, detail="您没有代肝数据，无法启用提醒")

        if rs:
            rs.enabled = enabled
            rs.push_time = push_time
        else:
            rs = ReminderSetting(user_id=user.id, enabled=enabled, push_time=push_time)
            session.add(rs)
        await session.flush()
        await audit(
            session, "user", user.name, "更新提醒设置",
            detail=f"enabled={enabled} time={push_time}",
            ip=await get_client_ip(request, session),
        )
        return {
            "enabled": rs.enabled,
            "push_time": rs.push_time,
            "last_sent_date": rs.last_sent_date,
        }


class SendMessageRequest(BaseModel):
    game_name: str
    content: str


@router.post("/me/messages")
async def send_message(
    body: SendMessageRequest, request: Request, payload: dict = Depends(require_user)
):
    """用户发送留言给管理员"""
    user_id = int(payload.get("sub"))

    async with get_session() as session:
        game_result = await session.execute(
            select(Game).where(Game.name == body.game_name)
        )
        game = game_result.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail=f"未找到游戏「{body.game_name}」")

        commission = await session.execute(
            select(Commission).where(
                Commission.user_id == user_id, Commission.game_id == game.id
            )
        )
        if not commission.scalar_one_or_none():
            raise HTTPException(
                status_code=403,
                detail=f"你尚未绑定「{body.game_name}」的代肝记录，无法留言",
            )

        msg = Message(user_id=user_id, game_id=game.id, content=body.content)
        session.add(msg)
        await session.flush()

        user_result = await session.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()

        await audit(
            session,
            "user",
            user.name if user else f"用户#{user_id}",
            "发送留言",
            target=f"游戏「{game.name}」",
            detail=body.content[:100],
            ip=await get_client_ip(request, session),
        )

    if user:
        try:
            import nonebot
            bot = nonebot.get_bot()
            superusers = nonebot.get_driver().config.superusers
            notify_text = (
                f"[代肝留言 (WebUI)]\n"
                f"用户：{user.name}（QQ:{user.qq_id or '未绑定'}）\n"
                f"游戏：{game.name}\n"
                f"内容：{body.content}"
            )
            for su_qq in superusers:
                try:
                    await bot.send_private_msg(user_id=int(su_qq), message=notify_text)
                except Exception:
                    pass
        except ValueError:
            pass

    return {"message": "留言已发送", "id": msg.id}


@router.get("/games")
async def list_games_for_user(payload: dict = Depends(require_user)):
    """获取当前用户已绑定代肝记录的游戏列表（供留言时选择）"""
    user_id = int(payload.get("sub"))
    async with get_session() as session:
        result = await session.execute(
            select(Game)
            .join(Commission, Commission.game_id == Game.id)
            .where(Commission.user_id == user_id)
            .options(selectinload(Game.group))
            .order_by(Game.name)
        )
        games = result.scalars().all()
        return [
            {
                "id": g.id,
                "name": g.name,
                "group_id": g.group_id,
                "group_name": g.group.name if g.group else None,
            }
            for g in games
        ]
