"""管理员 API 路由（数据管理）"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update

from ...database import get_session
from ...models import (
    Commission,
    Game,
    GameAlias,
    GameGroup,
    GroupCommission,
    Message,
    User,
    UserAlias,
)
from ...handlers.admin import find_user, find_game
from ..routes.auth import require_admin

from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---- 游戏组管理 ----

@router.get("/groups")
async def list_groups(_: dict = Depends(require_admin)):
    """获取所有游戏组（含组内游戏与别名）"""
    async with get_session() as session:
        result = await session.execute(
            select(GameGroup)
            .options(selectinload(GameGroup.games).selectinload(Game.aliases))
            .order_by(GameGroup.name)
        )
        groups = result.scalars().all()
        return [
            {
                "id": g.id,
                "name": g.name,
                "created_at": g.created_at.isoformat(),
                "games": [
                    {
                        "id": game.id,
                        "name": game.name,
                        "created_at": game.created_at.isoformat(),
                        "aliases": [a.alias for a in game.aliases],
                    }
                    for game in g.games
                ],
            }
            for g in groups
        ]


class CreateGroupRequest(BaseModel):
    name: str


@router.post("/groups")
async def create_group(body: CreateGroupRequest, _: dict = Depends(require_admin)):
    """创建游戏组"""
    async with get_session() as session:
        existing = await session.execute(select(GameGroup).where(GameGroup.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"游戏组「{body.name}」已存在")
        group = GameGroup(name=body.name)
        session.add(group)
        await session.flush()
        return {"id": group.id, "name": group.name}


class RenameGroupRequest(BaseModel):
    name: str


@router.patch("/groups/{group_id}")
async def rename_group(group_id: int, body: RenameGroupRequest, _: dict = Depends(require_admin)):
    """修改游戏组名称"""
    async with get_session() as session:
        result = await session.execute(select(GameGroup).where(GameGroup.id == group_id))
        group = result.scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="游戏组不存在")
        existing = await session.execute(select(GameGroup).where(GameGroup.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"游戏组「{body.name}」已存在")
        group.name = body.name
        return {"id": group.id, "name": group.name}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, _: dict = Depends(require_admin)):
    """删除游戏组（组内游戏变为未分组）"""
    async with get_session() as session:
        result = await session.execute(select(GameGroup).where(GameGroup.id == group_id))
        group = result.scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="游戏组不存在")
        await session.execute(
            update(Game).where(Game.group_id == group_id).values(group_id=None)
        )
        await session.delete(group)
    return {"message": "游戏组已删除"}


# ---- 用户管理 ----

@router.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    """获取所有用户列表（含别名）"""
    async with get_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.aliases)).order_by(User.name)
        )
        users = result.scalars().all()
        return [
            {
                "id": u.id,
                "name": u.name,
                "qq_id": u.qq_id,
                "created_at": u.created_at.isoformat(),
                "aliases": [a.alias for a in u.aliases],
            }
            for u in users
        ]


class CreateUserRequest(BaseModel):
    name: str


@router.post("/users")
async def create_user(body: CreateUserRequest, _: dict = Depends(require_admin)):
    """创建用户"""
    async with get_session() as session:
        existing = await session.execute(select(User).where(User.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"用户「{body.name}」已存在")
        user = User(name=body.name)
        session.add(user)
        await session.flush()
        return {"id": user.id, "name": user.name}


class UpdateUserRequest(BaseModel):
    qq_id: Optional[int] = None


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UpdateUserRequest, _: dict = Depends(require_admin)):
    """更新用户信息"""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        user.qq_id = body.qq_id
        return {"id": user.id, "name": user.name, "qq_id": user.qq_id}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, _: dict = Depends(require_admin)):
    """删除用户"""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        await session.delete(user)
    return {"message": "用户已删除"}


class CreateAliasRequest(BaseModel):
    alias: str

@router.post("/users/{user_id}/aliases")
async def add_user_alias(user_id: int, body: CreateAliasRequest, _: dict = Depends(require_admin)):
    """为用户添加别名"""
    async with get_session() as session:
        # 检查是否已被使用
        u_alias = await session.execute(select(UserAlias).where(UserAlias.alias == body.alias))
        if u_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他用户使用")
        g_alias = await session.execute(select(GameAlias).where(GameAlias.alias == body.alias))
        if g_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他游戏使用")
        
        session.add(UserAlias(user_id=user_id, alias=body.alias))
        return {"message": "别名添加成功"}

@router.delete("/users/{user_id}/aliases/{alias_name}")
async def delete_user_alias(user_id: int, alias_name: str, _: dict = Depends(require_admin)):
    """删除用户的别名"""
    async with get_session() as session:
        result = await session.execute(
            select(UserAlias).where(UserAlias.user_id == user_id, UserAlias.alias == alias_name)
        )
        alias_obj = result.scalar_one_or_none()
        if not alias_obj:
            raise HTTPException(status_code=404, detail="别名不存在")
        await session.delete(alias_obj)
        return {"message": "别名删除成功"}


# ---- 游戏管理 ----

@router.get("/games")
async def list_games(_: dict = Depends(require_admin)):
    """获取所有游戏列表（含别名与所属游戏组）"""
    async with get_session() as session:
        result = await session.execute(
            select(Game)
            .options(selectinload(Game.aliases), selectinload(Game.group))
            .order_by(Game.name)
        )
        games = result.scalars().all()
        return [
            {
                "id": g.id,
                "name": g.name,
                "created_at": g.created_at.isoformat(),
                "aliases": [a.alias for a in g.aliases],
                "group_id": g.group_id,
                "group_name": g.group.name if g.group else None,
            }
            for g in games
        ]


class CreateGameRequest(BaseModel):
    name: str
    group_id: Optional[int] = None


@router.post("/games")
async def create_game(body: CreateGameRequest, _: dict = Depends(require_admin)):
    """创建游戏（可指定所属游戏组）"""
    async with get_session() as session:
        existing = await session.execute(select(Game).where(Game.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"游戏「{body.name}」已存在")
        game = Game(name=body.name, group_id=body.group_id)
        session.add(game)
        await session.flush()
        return {"id": game.id, "name": game.name, "group_id": game.group_id}


class MoveGameRequest(BaseModel):
    group_id: Optional[int] = None


@router.patch("/games/{game_id}")
async def move_game(game_id: int, body: MoveGameRequest, _: dict = Depends(require_admin)):
    """将游戏移动到指定游戏组（group_id 为空则取消分组）"""
    async with get_session() as session:
        result = await session.execute(select(Game).where(Game.id == game_id))
        game = result.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="游戏不存在")
        game.group_id = body.group_id
        return {"id": game.id, "name": game.name, "group_id": game.group_id}


@router.delete("/games/{game_id}")
async def delete_game(game_id: int, _: dict = Depends(require_admin)):
    """删除游戏"""
    async with get_session() as session:
        result = await session.execute(select(Game).where(Game.id == game_id))
        game = result.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="游戏不存在")
        await session.delete(game)
    return {"message": "游戏已删除"}


@router.post("/games/{game_id}/aliases")
async def add_game_alias(game_id: int, body: CreateAliasRequest, _: dict = Depends(require_admin)):
    """为游戏添加别名"""
    async with get_session() as session:
        u_alias = await session.execute(select(UserAlias).where(UserAlias.alias == body.alias))
        if u_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他用户使用")
        g_alias = await session.execute(select(GameAlias).where(GameAlias.alias == body.alias))
        if g_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他游戏使用")
        
        session.add(GameAlias(game_id=game_id, alias=body.alias))
        return {"message": "别名添加成功"}

@router.delete("/games/{game_id}/aliases/{alias_name}")
async def delete_game_alias(game_id: int, alias_name: str, _: dict = Depends(require_admin)):
    """删除游戏的别名"""
    async with get_session() as session:
        result = await session.execute(
            select(GameAlias).where(GameAlias.game_id == game_id, GameAlias.alias == alias_name)
        )
        alias_obj = result.scalar_one_or_none()
        if not alias_obj:
            raise HTTPException(status_code=404, detail="别名不存在")
        await session.delete(alias_obj)
        return {"message": "别名删除成功"}


# ---- 代肝数据管理 ----

@router.get("/commissions")
async def list_commissions(_: dict = Depends(require_admin)):
    """获取所有代肝记录"""
    async with get_session() as session:
        result = await session.execute(
            select(Commission, User, Game)
            .join(User, Commission.user_id == User.id)
            .join(Game, Commission.game_id == Game.id)
            .options(selectinload(Game.group))
            .order_by(User.name, Game.name)
        )
        records = result.all()
        return [
            {
                "id": c.id,
                "user_id": c.user_id,
                "user_name": u.name,
                "game_id": c.game_id,
                "game_name": g.name,
                "group_id": g.group_id,
                "group_name": g.group.name if g.group else None,
                "completed_count": c.completed_count,
                "checked_in": c.checked_in,
                "last_checked_in_at": c.last_checked_in_at.isoformat() if c.last_checked_in_at else None,
            }
            for c, u, g in records
        ]


class UpdateCommissionRequest(BaseModel):
    completed_count: Optional[int] = None
    checked_in: Optional[bool] = None


@router.patch("/commissions/{commission_id}")
async def update_commission(commission_id: int, body: UpdateCommissionRequest, _: dict = Depends(require_admin)):
    """更新代肝记录"""
    async with get_session() as session:
        result = await session.execute(select(Commission).where(Commission.id == commission_id))
        comm = result.scalar_one_or_none()
        if not comm:
            raise HTTPException(status_code=404, detail="代肝记录不存在")
        if body.completed_count is not None:
            comm.completed_count = body.completed_count
        if body.checked_in is not None:
            comm.checked_in = body.checked_in
            if body.checked_in:
                comm.last_checked_in_at = datetime.now()
        return {"message": "更新成功"}


class CreateCommissionRequest(BaseModel):
    user_name: str
    game_name: str


@router.post("/commissions")
async def create_commission(body: CreateCommissionRequest, _: dict = Depends(require_admin)):
    """创建代肝记录（游戏级已完成跟踪，应得次数由游戏组记录）"""
    async with get_session() as session:
        user = await find_user(session, body.user_name)
        if not user:
            raise HTTPException(status_code=404, detail=f"未找到用户「{body.user_name}」")
        game = await find_game(session, body.game_name)
        if not game:
            raise HTTPException(status_code=404, detail=f"未找到游戏「{body.game_name}」")

        existing = await session.execute(
            select(Commission).where(Commission.user_id == user.id, Commission.game_id == game.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该用户游戏记录已存在")
        comm = Commission(user_id=user.id, game_id=game.id)
        session.add(comm)
        await session.flush()
        return {"id": comm.id}


@router.delete("/commissions/{commission_id}")
async def delete_commission(commission_id: int, _: dict = Depends(require_admin)):
    """删除代肝记录"""
    async with get_session() as session:
        result = await session.execute(select(Commission).where(Commission.id == commission_id))
        comm = result.scalar_one_or_none()
        if not comm:
            raise HTTPException(status_code=404, detail="代肝记录不存在")
        await session.delete(comm)
    return {"message": "记录已删除"}


# ---- 游戏组应得管理 ----

@router.get("/group-commissions")
async def list_group_commissions(_: dict = Depends(require_admin)):
    """获取所有游戏组应得记录"""
    async with get_session() as session:
        result = await session.execute(
            select(GroupCommission, User, GameGroup)
            .join(User, GroupCommission.user_id == User.id)
            .join(GameGroup, GroupCommission.game_group_id == GameGroup.id)
            .order_by(User.id, GameGroup.name)
        )
        records = result.all()
        return [
            {
                "id": gc.id,
                "user_id": u.id,
                "user_name": u.name,
                "game_group_id": g.id,
                "group_name": g.name,
                "total_count": gc.total_count,
            }
            for gc, u, g in records
        ]


class UpsertGroupCommissionRequest(BaseModel):
    user_name: str
    game_group_id: int
    total_count: int = 0


@router.post("/group-commissions")
async def upsert_group_commission(
    body: UpsertGroupCommissionRequest, _: dict = Depends(require_admin)
):
    """设置/更新用户在游戏组下的应得次数（按用户+游戏组 upsert）"""
    async with get_session() as session:
        user = await find_user(session, body.user_name)
        if not user:
            raise HTTPException(status_code=404, detail=f"未找到用户「{body.user_name}」")
        group = await session.execute(select(GameGroup).where(GameGroup.id == body.game_group_id))
        grp = group.scalar_one_or_none()
        if not grp:
            raise HTTPException(status_code=404, detail="游戏组不存在")

        existing = await session.execute(
            select(GroupCommission).where(
                GroupCommission.user_id == user.id,
                GroupCommission.game_group_id == grp.id,
            )
        )
        gc = existing.scalar_one_or_none()
        if gc:
            gc.total_count = body.total_count
        else:
            gc = GroupCommission(
                user_id=user.id, game_group_id=grp.id, total_count=body.total_count
            )
            session.add(gc)
        await session.flush()
        return {
            "id": gc.id,
            "user_id": user.id,
            "user_name": user.name,
            "game_group_id": grp.id,
            "group_name": grp.name,
            "total_count": gc.total_count,
        }


class UpdateGroupCommissionRequest(BaseModel):
    total_count: int


@router.patch("/group-commissions/{group_commission_id}")
async def update_group_commission(
    group_commission_id: int,
    body: UpdateGroupCommissionRequest,
    _: dict = Depends(require_admin),
):
    """更新游戏组应得次数"""
    async with get_session() as session:
        result = await session.execute(
            select(GroupCommission).where(GroupCommission.id == group_commission_id)
        )
        gc = result.scalar_one_or_none()
        if not gc:
            raise HTTPException(status_code=404, detail="应得记录不存在")
        gc.total_count = max(0, body.total_count)
        return {"id": gc.id, "total_count": gc.total_count}


@router.delete("/group-commissions/{group_commission_id}")
async def delete_group_commission(
    group_commission_id: int, _: dict = Depends(require_admin)
):
    """删除游戏组应得记录"""
    async with get_session() as session:
        result = await session.execute(
            select(GroupCommission).where(GroupCommission.id == group_commission_id)
        )
        gc = result.scalar_one_or_none()
        if not gc:
            raise HTTPException(status_code=404, detail="应得记录不存在")
        await session.delete(gc)
    return {"message": "应得记录已删除"}


class CheckinRequest(BaseModel):
    user_id: int
    game_id: int
    count: int = 1

@router.post("/checkin")
async def admin_checkin(body: CheckinRequest, _: dict = Depends(require_admin)):
    """管理员为用户打卡"""
    async with get_session() as session:
        result = await session.execute(
            select(Commission).where(Commission.user_id == body.user_id, Commission.game_id == body.game_id)
        )
        comm = result.scalar_one_or_none()
        if not comm:
            raise HTTPException(status_code=404, detail="代肝记录不存在")
        comm.completed_count += body.count
        comm.checked_in = True
        comm.last_checked_in_at = datetime.now()
        return {"message": "打卡成功"}


# ---- 留言管理 ----

@router.get("/messages")
async def list_messages(unread_only: bool = False, _: dict = Depends(require_admin)):
    """获取所有留言"""
    async with get_session() as session:
        query = (
            select(Message, User, Game)
            .join(User, Message.user_id == User.id)
            .join(Game, Message.game_id == Game.id)
            .order_by(Message.created_at.desc())
        )
        if unread_only:
            query = query.where(Message.is_read == False)
        result = await session.execute(query)
        records = result.all()
        return [
            {
                "id": m.id,
                "user_id": m.user_id,
                "user_name": u.name,
                "game_id": m.game_id,
                "game_name": g.name,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "is_read": m.is_read,
            }
            for m, u, g in records
        ]

@router.patch("/messages/{message_id}/read")
async def mark_message_read(message_id: int, _: dict = Depends(require_admin)):
    """标记留言为已读"""
    async with get_session() as session:
        result = await session.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one_or_none()
        if not msg:
            raise HTTPException(status_code=404, detail="留言不存在")
        msg.is_read = True
    return {"message": "已标记为已读"}
