"""用户 API 路由"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ...database import get_session
from ...models import (
    Commission,
    Game,
    GameGroup,
    GroupCommission,
    Message,
    User,
)
from ..routes.auth import require_user
from ..utils import audit, get_client_ip

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me/commissions")
async def get_my_commissions(payload: dict = Depends(require_user)):
    """获取当前用户的代肝记录（管理员查看时返回空）"""
    if payload.get("role") == "admin":
        return []

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
    """获取当前用户在游戏组下的应得次数（管理员查看时返回空）"""
    if payload.get("role") == "admin":
        return []

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
    """获取当前用户今日打卡进度（管理员查看时返回空）"""
    if payload.get("role") == "admin":
        return []

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
    """获取当前用户的历史留言（含已读状态，管理员查看时返回空）"""
    if payload.get("role") == "admin":
        return []

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


class SendMessageRequest(BaseModel):
    game_name: str
    content: str


@router.post("/me/messages")
async def send_message(
    body: SendMessageRequest, request: Request, payload: dict = Depends(require_user)
):
    """用户发送留言给管理员"""
    if payload.get("role") == "admin":
        raise HTTPException(status_code=403, detail="管理员无需通过此接口留言")

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
    """获取当前用户已绑定代肝记录的游戏列表（供留言时选择，管理员查看时返回空）"""
    if payload.get("role") == "admin":
        return []
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
