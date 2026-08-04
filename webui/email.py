"""SMTP 邮件发送与验证码"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select

from ..database import get_session
from ..models import EmailCode, SystemSettings

EMAIL_CODE_EXPIRE_MINUTES = 10


class EmailNotConfiguredError(Exception):
    """邮箱服务未配置"""


async def _get_smtp_config(session) -> dict:
    result = await session.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    s = result.scalar_one_or_none()
    if not s:
        s = SystemSettings(id=1)
        session.add(s)
        await session.flush()
    return {
        "host": s.smtp_host,
        "port": s.smtp_port,
        "user": s.smtp_user,
        "password": s.smtp_password,
        "from": s.smtp_from,
        "security": s.smtp_security or "starttls",
    }


async def send_email(to: str, subject: str, text: str) -> None:
    """发送邮件"""
    import aiosmtplib

    async with get_session() as session:
        cfg = await _get_smtp_config(session)

    if not cfg["host"] or not cfg["from"]:
        raise EmailNotConfiguredError("邮箱服务未配置")

    security = cfg["security"] or "starttls"
    await aiosmtplib.send(
        subject=subject,
        message=text,
        from_addr=cfg["from"],
        to_addrs=[to],
        hostname=cfg["host"],
        port=cfg["port"],
        username=cfg["user"] or None,
        password=cfg["password"] or None,
        use_tls=security == "tls",
        start_tls=security == "starttls",
    )


async def issue_email_code(session, email: str, purpose: str) -> str:
    """生成并保存邮箱验证码"""
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    expires_at = datetime.now() + timedelta(minutes=EMAIL_CODE_EXPIRE_MINUTES)
    session.add(
        EmailCode(email=email, purpose=purpose, code=code, expires_at=expires_at)
    )
    return code


async def verify_email_code(session, email: str, purpose: str, code: str) -> bool:
    """校验邮箱验证码（成功后标记已使用）"""
    if not code:
        return False
    result = await session.execute(
        select(EmailCode)
        .where(
            EmailCode.email == email,
            EmailCode.purpose == purpose,
            EmailCode.code == code,
            EmailCode.is_used == False,
            EmailCode.expires_at > datetime.now(),
        )
        .order_by(EmailCode.id.desc())
    )
    row = result.scalars().first()
    if not row:
        return False
    row.is_used = True
    return True
