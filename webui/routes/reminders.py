"""定时提醒管理 API（管理员）"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from ...database import get_session
from ...handlers.admin import find_user
from ...models import Commission, DailyNote, ReminderSetting, User
from ..reminder import DEFAULT_REMINDER_TEMPLATE, get_template, set_template
from ..utils import audit, get_client_ip
from .auth import require_admin

router = APIRouter(prefix="/api/admin/reminders", tags=["reminders"])

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ReminderUpsertRequest(BaseModel):
    user_name: str
    push_time: str
    enabled: bool = True


class ReminderUpdateRequest(BaseModel):
    push_time: str | None = None
    enabled: bool | None = None


class TemplateRequest(BaseModel):
    template: str


class NoteRequest(BaseModel):
    content: str = ""


async def _validate_time(value: str) -> str:
    if not TIME_RE.match(value):
        raise HTTPException(status_code=400, detail="推送时间格式应为 HH:MM（如 22:00）")
    return value


async def _validate_eligible(session, user: User) -> None:
    """推送用户必须绑定 QQ 且有代肝数据"""
    if not user.qq_id:
        raise HTTPException(status_code=400, detail=f"用户「{user.name}」未绑定 QQ，无法推送")
    result = await session.execute(
        select(Commission.id).where(Commission.user_id == user.id).limit(1)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=400, detail=f"用户「{user.name}」没有代肝数据，无法推送")


@router.get("")
async def list_reminders(_: dict = Depends(require_admin)):
    """列出所有提醒设置（含用户信息与最后发送日期）"""
    async with get_session() as session:
        result = await session.execute(
            select(ReminderSetting, User)
            .join(User, ReminderSetting.user_id == User.id)
            .order_by(User.name)
        )
        return [
            {
                "user_id": u.id,
                "user_name": u.name,
                "qq_id": u.qq_id,
                "enabled": rs.enabled,
                "push_time": rs.push_time,
                "last_sent_date": rs.last_sent_date,
            }
            for rs, u in result.all()
        ]


@router.post("")
async def upsert_reminder(
    body: ReminderUpsertRequest, request: Request, _: dict = Depends(require_admin)
):
    """按用户名创建/更新提醒设置"""
    push_time = await _validate_time(body.push_time)
    async with get_session() as session:
        user = await find_user(session, body.user_name.strip())
        if not user:
            raise HTTPException(status_code=404, detail=f"未找到用户「{body.user_name.strip()}」")
        await _validate_eligible(session, user)

        result = await session.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == user.id)
        )
        rs = result.scalar_one_or_none()
        if rs:
            rs.push_time = push_time
            rs.enabled = body.enabled
            action = "更新提醒设置"
        else:
            rs = ReminderSetting(
                user_id=user.id, push_time=push_time, enabled=body.enabled
            )
            session.add(rs)
            action = "创建提醒设置"
        await session.flush()
        await audit(
            session, "admin", "admin", action,
            target=f"用户「{user.name}」",
            detail=f"time={push_time} enabled={body.enabled}",
            ip=await get_client_ip(request, session),
        )
        return {
            "user_id": user.id,
            "user_name": user.name,
            "qq_id": user.qq_id,
            "enabled": rs.enabled,
            "push_time": rs.push_time,
            "last_sent_date": rs.last_sent_date,
        }


@router.patch("/{user_id}")
async def update_reminder(
    user_id: int, body: ReminderUpdateRequest, request: Request, _: dict = Depends(require_admin)
):
    """更新指定用户的提醒设置"""
    async with get_session() as session:
        result = await session.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        )
        rs = result.scalar_one_or_none()
        if not rs:
            raise HTTPException(status_code=404, detail="提醒设置不存在")
        if body.push_time is not None:
            rs.push_time = await _validate_time(body.push_time)
        if body.enabled is not None:
            rs.enabled = body.enabled
        await audit(
            session, "admin", "admin", "更新提醒设置",
            target=f"用户#{user_id}",
            detail=f"time={rs.push_time} enabled={rs.enabled}",
            ip=await get_client_ip(request, session),
        )
        return {
            "user_id": user_id,
            "enabled": rs.enabled,
            "push_time": rs.push_time,
        }


@router.delete("/{user_id}")
async def delete_reminder(user_id: int, request: Request, _: dict = Depends(require_admin)):
    """删除用户的提醒设置"""
    async with get_session() as session:
        result = await session.execute(
            select(ReminderSetting).where(ReminderSetting.user_id == user_id)
        )
        rs = result.scalar_one_or_none()
        if not rs:
            raise HTTPException(status_code=404, detail="提醒设置不存在")
        await session.delete(rs)
        await audit(
            session, "admin", "admin", "删除提醒设置",
            target=f"用户#{user_id}", ip=await get_client_ip(request, session),
        )
    return {"message": "提醒设置已删除"}


@router.get("/template")
async def get_reminder_template(_: dict = Depends(require_admin)):
    async with get_session() as session:
        return {"template": await get_template(session)}


@router.put("/template")
async def put_reminder_template(
    body: TemplateRequest, request: Request, _: dict = Depends(require_admin)
):
    async with get_session() as session:
        template = await set_template(session, body.template)
        await audit(
            session, "admin", "admin", "修改提醒模板",
            ip=await get_client_ip(request, session),
        )
    return {"template": template}


@router.post("/template/reset")
async def reset_reminder_template(request: Request, _: dict = Depends(require_admin)):
    async with get_session() as session:
        template = await set_template(session, DEFAULT_REMINDER_TEMPLATE)
        await audit(
            session, "admin", "admin", "重置提醒模板",
            ip=await get_client_ip(request, session),
        )
    return {"template": template}


# ---- 当日备注 ----

@router.get("/notes")
async def list_today_notes(_: dict = Depends(require_admin)):
    """返回今日各用户备注映射 {user_id: content}"""
    today = datetime.now().strftime("%Y-%m-%d")
    async with get_session() as session:
        result = await session.execute(
            select(DailyNote).where(DailyNote.note_date == today)
        )
        notes = result.scalars().all()
        return {note.user_id: note.content for note in notes}


@router.put("/notes/{user_id}")
async def set_today_note(
    user_id: int, body: NoteRequest, request: Request, _: dict = Depends(require_admin)
):
    """设置/清除用户当日备注（空内容清除）"""
    today = datetime.now().strftime("%Y-%m-%d")
    content = body.content.strip()
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        result = await session.execute(
            select(DailyNote).where(
                DailyNote.user_id == user_id, DailyNote.note_date == today
            )
        )
        note = result.scalar_one_or_none()
        if content:
            if note:
                note.content = content
            else:
                session.add(DailyNote(user_id=user_id, note_date=today, content=content))
        else:
            if note:
                await session.delete(note)
        await audit(
            session, "admin", "admin", "设置当日备注",
            target=f"用户「{user.name}」",
            detail=content[:50] or "清除",
            ip=await get_client_ip(request, session),
        )
    return {"message": "备注已更新", "content": content}
