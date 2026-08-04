"""管理员 API 路由（数据管理）"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import or_, select, update

from ...database import get_session
from ...models import (
    AuditLog,
    Commission,
    Game,
    GameAlias,
    GameGroup,
    GroupCommission,
    Message,
    SystemSettings,
    User,
    UserAlias,
)
from ...handlers.admin import find_user, find_game
from ..routes.auth import require_admin
from ..utils import audit, get_client_ip

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
async def create_group(body: CreateGroupRequest, request: Request, _: dict = Depends(require_admin)):
    """创建游戏组"""
    async with get_session() as session:
        existing = await session.execute(select(GameGroup).where(GameGroup.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"游戏组「{body.name}」已存在")
        group = GameGroup(name=body.name)
        session.add(group)
        await session.flush()
        await audit(
            session,
            "admin",
            "admin",
            "创建游戏组",
            target=f"游戏组「{group.name}」",
            ip=await get_client_ip(request, session),
        )
        return {"id": group.id, "name": group.name}


class RenameGroupRequest(BaseModel):
    name: str


@router.patch("/groups/{group_id}")
async def rename_group(group_id: int, body: RenameGroupRequest, request: Request, _: dict = Depends(require_admin)):
    """修改游戏组名称"""
    async with get_session() as session:
        result = await session.execute(select(GameGroup).where(GameGroup.id == group_id))
        group = result.scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="游戏组不存在")
        existing = await session.execute(select(GameGroup).where(GameGroup.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"游戏组「{body.name}」已存在")
        old_name = group.name
        group.name = body.name
        await audit(
            session,
            "admin",
            "admin",
            "修改游戏组名",
            target=f"游戏组「{old_name}」",
            detail=body.name,
            ip=await get_client_ip(request, session),
        )
        return {"id": group.id, "name": group.name}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, request: Request, _: dict = Depends(require_admin)):
    """删除游戏组（组内游戏变为未分组）"""
    async with get_session() as session:
        result = await session.execute(select(GameGroup).where(GameGroup.id == group_id))
        group = result.scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail="游戏组不存在")
        await session.execute(
            update(Game).where(Game.group_id == group_id).values(group_id=None)
        )
        await audit(
            session,
            "admin",
            "admin",
            "删除游戏组",
            target=f"游戏组「{group.name}」",
            ip=await get_client_ip(request, session),
        )
        await session.delete(group)
    return {"message": "游戏组已删除"}


# ---- 用户管理 ----

@router.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    """获取所有用户列表（含管理员，统一为 User 行）"""
    async with get_session() as session:
        result = await session.execute(
            select(User).options(selectinload(User.aliases)).order_by(User.name)
        )
        users = result.scalars().all()
        return [
            {
                "id": u.id,
                "name": u.name,
                "role": u.role,
                "is_admin": u.role == "admin",
                "qq_id": u.qq_id,
                "email": u.email,
                "email_verified": u.email_verified,
                "login_disabled": u.login_disabled,
                "created_at": u.created_at.isoformat(),
                "aliases": [a.alias for a in u.aliases],
            }
            for u in users
        ]


class CreateUserRequest(BaseModel):
    name: str


@router.post("/users")
async def create_user(body: CreateUserRequest, request: Request, _: dict = Depends(require_admin)):
    """创建用户"""
    async with get_session() as session:
        existing = await session.execute(select(User).where(User.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"用户「{body.name}」已存在")
        user = User(name=body.name, role="user")
        session.add(user)
        await session.flush()
        await audit(
            session,
            "admin",
            "admin",
            "创建用户",
            target=f"用户「{user.name}」",
            ip=await get_client_ip(request, session),
        )
        return {"id": user.id, "name": user.name, "role": user.role}


class UpdateUserRequest(BaseModel):
    qq_id: Optional[int] = None
    name: Optional[str] = None
    move_old_to_alias: Optional[bool] = False
    email: Optional[str] = None
    login_disabled: Optional[bool] = None


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UpdateUserRequest, request: Request, _: dict = Depends(require_admin)):
    """更新用户信息：改名（可移入别名）、QQ、邮箱、停用登录（管理员不能被停用）"""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        if body.login_disabled is True and user.role == "admin":
            raise HTTPException(status_code=400, detail="管理员不能被停用")

        changes = []
        if body.qq_id is not None:
            user.qq_id = body.qq_id
            changes.append(f"qq_id={body.qq_id}")

        if body.name is not None:
            new_name = body.name.strip()
            if not new_name:
                raise HTTPException(status_code=400, detail="用户名不能为空")
            if new_name != user.name:
                existing = await session.execute(select(User).where(User.name == new_name))
                if existing.scalar_one_or_none():
                    raise HTTPException(status_code=400, detail=f"用户名「{new_name}」已存在")
                old_name = user.name
                if body.move_old_to_alias:
                    dup = await session.execute(
                        select(UserAlias).where(UserAlias.alias == old_name)
                    )
                    if dup.scalar_one_or_none():
                        raise HTTPException(status_code=400, detail="原名称已被其他别名占用，无法自动加入别名")
                    session.add(UserAlias(user_id=user.id, alias=old_name))
                user.name = new_name
                changes.append(f"改名: {old_name} -> {new_name}")

        if body.email is not None:
            email = body.email.strip().lower() or None
            if email:
                u = await session.execute(
                    select(User).where(User.email == email, User.id != user.id)
                )
                if u.scalar_one_or_none():
                    raise HTTPException(status_code=400, detail=f"邮箱 {email} 已被其他用户使用")
            user.email = email
            user.email_verified = True
            changes.append(f"email={email}")

        if body.login_disabled is not None:
            user.login_disabled = body.login_disabled
            changes.append(f"login_disabled={body.login_disabled}")

        await audit(
            session, "admin", "admin", "更新用户",
            target=f"用户「{user.name}」", detail=", ".join(changes) or "无变更",
            ip=await get_client_ip(request, session),
        )
        return {
            "id": user.id,
            "name": user.name,
            "role": user.role,
            "qq_id": user.qq_id,
            "email": user.email,
            "login_disabled": user.login_disabled,
        }


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, _: dict = Depends(require_admin)):
    """删除用户"""
    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.role == "admin":
            raise HTTPException(status_code=400, detail="不能删除管理员")
        await audit(
            session,
            "admin",
            "admin",
            "删除用户",
            target=f"用户「{user.name}」",
            ip=await get_client_ip(request, session),
        )
        await session.delete(user)
    return {"message": "用户已删除"}


class CreateAliasRequest(BaseModel):
    alias: str

@router.post("/users/{user_id}/aliases")
async def add_user_alias(user_id: int, body: CreateAliasRequest, request: Request, _: dict = Depends(require_admin)):
    """为用户添加别名"""
    async with get_session() as session:
        # 检查是否已被使用
        u_alias = await session.execute(select(UserAlias).where(UserAlias.alias == body.alias))
        if u_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他用户使用")
        g_alias = await session.execute(select(GameAlias).where(GameAlias.alias == body.alias))
        if g_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他游戏使用")
        
        user = await session.execute(select(User).where(User.id == user_id))
        u = user.scalar_one_or_none()
        if not u:
            raise HTTPException(status_code=404, detail="用户不存在")

        session.add(UserAlias(user_id=user_id, alias=body.alias))
        await audit(
            session,
            "admin",
            "admin",
            "添加用户别名",
            target=f"用户「{u.name}」",
            detail=f"别名「{body.alias}」",
            ip=await get_client_ip(request, session),
        )
        return {"message": "别名添加成功"}

@router.delete("/users/{user_id}/aliases/{alias_name}")
async def delete_user_alias(user_id: int, alias_name: str, request: Request, _: dict = Depends(require_admin)):
    """删除用户的别名"""
    async with get_session() as session:
        result = await session.execute(
            select(UserAlias).where(UserAlias.user_id == user_id, UserAlias.alias == alias_name)
        )
        alias_obj = result.scalar_one_or_none()
        if not alias_obj:
            raise HTTPException(status_code=404, detail="别名不存在")
        user = await session.execute(select(User).where(User.id == user_id))
        u = user.scalar_one_or_none()
        await session.delete(alias_obj)
        await audit(
            session,
            "admin",
            "admin",
            "删除用户别名",
            target=f"用户「{u.name if u else user_id}」",
            detail=f"别名「{alias_name}」",
            ip=await get_client_ip(request, session),
        )
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
async def create_game(body: CreateGameRequest, request: Request, _: dict = Depends(require_admin)):
    """创建游戏（可指定所属游戏组）"""
    async with get_session() as session:
        existing = await session.execute(select(Game).where(Game.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"游戏「{body.name}」已存在")
        game = Game(name=body.name, group_id=body.group_id)
        session.add(game)
        await session.flush()
        await audit(
            session,
            "admin",
            "admin",
            "创建游戏",
            target=f"游戏「{game.name}」",
            detail=f"group_id={body.group_id}",
            ip=await get_client_ip(request, session),
        )
        return {"id": game.id, "name": game.name, "group_id": game.group_id}


class MoveGameRequest(BaseModel):
    group_id: Optional[int] = None


@router.patch("/games/{game_id}")
async def move_game(game_id: int, body: MoveGameRequest, request: Request, _: dict = Depends(require_admin)):
    """将游戏移动到指定游戏组（group_id 为空则取消分组）"""
    async with get_session() as session:
        result = await session.execute(select(Game).where(Game.id == game_id))
        game = result.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="游戏不存在")
        old_group = game.group_id
        game.group_id = body.group_id
        await audit(
            session,
            "admin",
            "admin",
            "移动游戏",
            target=f"游戏「{game.name}」",
            detail=f"group_id: {old_group} -> {body.group_id}",
            ip=await get_client_ip(request, session),
        )
        return {"id": game.id, "name": game.name, "group_id": game.group_id}


@router.delete("/games/{game_id}")
async def delete_game(game_id: int, request: Request, _: dict = Depends(require_admin)):
    """删除游戏"""
    async with get_session() as session:
        result = await session.execute(select(Game).where(Game.id == game_id))
        game = result.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="游戏不存在")
        await audit(
            session,
            "admin",
            "admin",
            "删除游戏",
            target=f"游戏「{game.name}」",
            ip=await get_client_ip(request, session),
        )
        await session.delete(game)
    return {"message": "游戏已删除"}


@router.post("/games/{game_id}/aliases")
async def add_game_alias(game_id: int, body: CreateAliasRequest, request: Request, _: dict = Depends(require_admin)):
    """为游戏添加别名"""
    async with get_session() as session:
        u_alias = await session.execute(select(UserAlias).where(UserAlias.alias == body.alias))
        if u_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他用户使用")
        g_alias = await session.execute(select(GameAlias).where(GameAlias.alias == body.alias))
        if g_alias.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"别名「{body.alias}」已被其他游戏使用")
        
        game = await session.execute(select(Game).where(Game.id == game_id))
        g = game.scalar_one_or_none()
        if not g:
            raise HTTPException(status_code=404, detail="游戏不存在")

        session.add(GameAlias(game_id=game_id, alias=body.alias))
        await audit(
            session,
            "admin",
            "admin",
            "添加游戏别名",
            target=f"游戏「{g.name}」",
            detail=f"别名「{body.alias}」",
            ip=await get_client_ip(request, session),
        )
        return {"message": "别名添加成功"}

@router.delete("/games/{game_id}/aliases/{alias_name}")
async def delete_game_alias(game_id: int, alias_name: str, request: Request, _: dict = Depends(require_admin)):
    """删除游戏的别名"""
    async with get_session() as session:
        result = await session.execute(
            select(GameAlias).where(GameAlias.game_id == game_id, GameAlias.alias == alias_name)
        )
        alias_obj = result.scalar_one_or_none()
        if not alias_obj:
            raise HTTPException(status_code=404, detail="别名不存在")
        game = await session.execute(select(Game).where(Game.id == game_id))
        g = game.scalar_one_or_none()
        await session.delete(alias_obj)
        await audit(
            session,
            "admin",
            "admin",
            "删除游戏别名",
            target=f"游戏「{g.name if g else game_id}」",
            detail=f"别名「{alias_name}」",
            ip=await get_client_ip(request, session),
        )
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
async def update_commission(commission_id: int, body: UpdateCommissionRequest, request: Request, _: dict = Depends(require_admin)):
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
        await audit(
            session,
            "admin",
            "admin",
            "更新代肝记录",
            target=f"代肝记录#{commission_id}",
            detail=f"completed_count={body.completed_count} checked_in={body.checked_in}",
            ip=await get_client_ip(request, session),
        )
        return {"message": "更新成功"}


class CreateCommissionRequest(BaseModel):
    user_name: str
    game_name: str


@router.post("/commissions")
async def create_commission(body: CreateCommissionRequest, request: Request, _: dict = Depends(require_admin)):
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
        await audit(
            session,
            "admin",
            "admin",
            "添加代肝记录",
            target=f"用户「{user.name}」- 游戏「{game.name}」",
            ip=await get_client_ip(request, session),
        )
        return {"id": comm.id}


@router.delete("/commissions/{commission_id}")
async def delete_commission(commission_id: int, request: Request, _: dict = Depends(require_admin)):
    """删除代肝记录"""
    async with get_session() as session:
        result = await session.execute(select(Commission).where(Commission.id == commission_id))
        comm = result.scalar_one_or_none()
        if not comm:
            raise HTTPException(status_code=404, detail="代肝记录不存在")
        await audit(
            session,
            "admin",
            "admin",
            "删除代肝记录",
            target=f"代肝记录#{commission_id}",
            ip=await get_client_ip(request, session),
        )
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
    body: UpsertGroupCommissionRequest,
    request: Request,
    _: dict = Depends(require_admin),
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
        await audit(
            session,
            "admin",
            "admin",
            "设置应得次数",
            target=f"用户「{user.name}」- 游戏组「{grp.name}」",
            detail=f"total_count={body.total_count}",
            ip=await get_client_ip(request, session),
        )
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
    request: Request,
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
        await audit(
            session,
            "admin",
            "admin",
            "更新应得次数",
            target=f"应得记录#{group_commission_id}",
            detail=f"total_count={gc.total_count}",
            ip=await get_client_ip(request, session),
        )
        return {"id": gc.id, "total_count": gc.total_count}


@router.delete("/group-commissions/{group_commission_id}")
async def delete_group_commission(
    group_commission_id: int,
    request: Request,
    _: dict = Depends(require_admin),
):
    """删除游戏组应得记录"""
    async with get_session() as session:
        result = await session.execute(
            select(GroupCommission).where(GroupCommission.id == group_commission_id)
        )
        gc = result.scalar_one_or_none()
        if not gc:
            raise HTTPException(status_code=404, detail="应得记录不存在")
        await audit(
            session,
            "admin",
            "admin",
            "删除应得记录",
            target=f"应得记录#{group_commission_id}",
            ip=await get_client_ip(request, session),
        )
        await session.delete(gc)
    return {"message": "应得记录已删除"}


class CheckinRequest(BaseModel):
    user_id: int
    game_id: int
    count: int = 1

@router.post("/checkin")
async def admin_checkin(body: CheckinRequest, request: Request, _: dict = Depends(require_admin)):
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

        user = await session.execute(select(User).where(User.id == body.user_id))
        u = user.scalar_one_or_none()
        game = await session.execute(select(Game).where(Game.id == body.game_id))
        g = game.scalar_one_or_none()

        await audit(
            session,
            "admin",
            "admin",
            "打卡",
            target=f"用户「{u.name if u else body.user_id}」- 游戏「{g.name if g else body.game_id}」",
            detail=f"count=+{body.count} completed_count={comm.completed_count}",
            ip=await get_client_ip(request, session),
        )
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
async def mark_message_read(message_id: int, request: Request, _: dict = Depends(require_admin)):
    """标记留言为已读"""
    async with get_session() as session:
        result = await session.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one_or_none()
        if not msg:
            raise HTTPException(status_code=404, detail="留言不存在")
        msg.is_read = True
        await audit(
            session,
            "admin",
            "admin",
            "标记留言已读",
            target=f"留言#{message_id}",
            ip=await get_client_ip(request, session),
        )
    return {"message": "已标记为已读"}


# ---- 系统设置 ----

class EmailTestRequest(BaseModel):
    to: str


@router.post("/email/test")
async def test_email(body: EmailTestRequest, request: Request, _: dict = Depends(require_admin)):
    """发送测试邮件，验证邮箱服务可用性"""
    from ..email import EmailNotConfiguredError, send_email

    target = body.to.strip()
    if "@" not in target or "." not in target.split("@")[-1]:
        raise HTTPException(status_code=400, detail="收件邮箱格式不正确")
    try:
        await send_email(target, "代肝记录系统 - 测试邮件", "这是一封测试邮件，若您收到说明邮箱服务配置正确。")
    except EmailNotConfiguredError:
        raise HTTPException(status_code=400, detail="邮箱服务未配置，请先填写 SMTP 信息")
    except Exception:
        raise HTTPException(status_code=500, detail="发送失败，请检查 SMTP 配置")
    async with get_session() as session:
        await audit(
            session, "admin", "admin", "测试邮件",
            target=target, ip=await get_client_ip(request, session),
        )
    return {"message": "测试邮件已发送"}


class UpdateSettingsRequest(BaseModel):
    reverse_proxy: Optional[bool] = None
    allow_avatar_upload: Optional[bool] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_security: Optional[str] = None
    allow_email_binding: Optional[bool] = None
    allow_forgot_password: Optional[bool] = None
    passkey_enabled: Optional[bool] = None
    passkey_rp_ids: Optional[list[str]] = None
    passkey_allow_http: Optional[bool] = None


def _settings_to_dict(s) -> dict:
    import json

    try:
        rp_ids = json.loads(s.passkey_rp_ids or "[]")
    except Exception:
        rp_ids = []
    return {
        "reverse_proxy": s.reverse_proxy,
        "allow_avatar_upload": s.allow_avatar_upload,
        "smtp_host": s.smtp_host,
        "smtp_port": s.smtp_port,
        "smtp_user": s.smtp_user,
        "smtp_password": s.smtp_password,
        "smtp_from": s.smtp_from,
        "smtp_security": s.smtp_security,
        "allow_email_binding": s.allow_email_binding,
        "allow_forgot_password": s.allow_forgot_password,
        "passkey_enabled": s.passkey_enabled,
        "passkey_rp_ids": rp_ids,
        "passkey_allow_http": s.passkey_allow_http,
    }


@router.get("/settings")
async def get_settings(_: dict = Depends(require_admin)):
    """获取系统设置"""
    async with get_session() as session:
        result = await session.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )
        settings = result.scalar_one_or_none()
        if not settings:
            settings = SystemSettings(id=1)
            session.add(settings)
            await session.flush()
        return _settings_to_dict(settings)


@router.put("/settings")
async def update_settings(body: UpdateSettingsRequest, request: Request, _: dict = Depends(require_admin)):
    """更新系统设置"""
    import json

    async with get_session() as session:
        result = await session.execute(
            select(SystemSettings).where(SystemSettings.id == 1)
        )
        settings = result.scalar_one_or_none()
        if not settings:
            settings = SystemSettings(id=1)
            session.add(settings)
        changes = []
        for field in [
            "reverse_proxy",
            "allow_avatar_upload",
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "smtp_from",
            "smtp_security",
            "allow_email_binding",
            "allow_forgot_password",
            "passkey_enabled",
            "passkey_allow_http",
        ]:
            value = getattr(body, field)
            if value is not None:
                setattr(settings, field, value)
                changes.append(f"{field}={value}")
        if body.passkey_rp_ids is not None:
            settings.passkey_rp_ids = json.dumps(body.passkey_rp_ids, ensure_ascii=False)
            changes.append(f"passkey_rp_ids={body.passkey_rp_ids}")
        await session.flush()
        await audit(
            session,
            "admin",
            "admin",
            "修改系统设置",
            detail=", ".join(changes) or "无变更",
            ip=await get_client_ip(request, session),
        )
        return _settings_to_dict(settings)


# ---- 审计日志 ----

@router.get("/audit-logs")
async def list_audit_logs(
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    _: dict = Depends(require_admin),
):
    """获取审计日志（按时间倒序）"""
    async with get_session() as session:
        query = select(AuditLog).order_by(AuditLog.id.desc())
        if q.strip():
            like = f"%{q.strip()}%"
            query = query.where(
                or_(
                    AuditLog.actor_name.like(like),
                    AuditLog.action.like(like),
                    AuditLog.target.like(like),
                    AuditLog.detail.like(like),
                    AuditLog.ip.like(like),
                )
            )
        query = query.offset(max(0, offset)).limit(min(max(1, limit), 500))
        result = await session.execute(query)
        logs = result.scalars().all()
        return [
            {
                "id": l.id,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "actor_type": l.actor_type,
                "actor_name": l.actor_name,
                "action": l.action,
                "target": l.target,
                "detail": l.detail,
                "ip": l.ip,
            }
            for l in logs
        ]
