"""SQLAlchemy ORM 模型定义"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 角色：user / admin（admin 即登录账号本身，仅一个）
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    qq_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    # 账号密码登录（由用户在个人设置中设置，未设置则仅能通过验证码登录）
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 邮箱（二期启用，可空）
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 上传头像文件名（位于 data/avatars/ 下），为空时回退 QQ 头像
    avatar_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 是否停用 WebUI 登录（管理员不受影响）
    login_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    aliases: Mapped[list["UserAlias"]] = relationship(
        "UserAlias", back_populates="user", cascade="all, delete-orphan"
    )
    commissions: Mapped[list["Commission"]] = relationship(
        "Commission", back_populates="user", cascade="all, delete-orphan"
    )
    group_commissions: Mapped[list["GroupCommission"]] = relationship(
        "GroupCommission", back_populates="user", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="user", cascade="all, delete-orphan"
    )
    login_codes: Mapped[list["LoginCode"]] = relationship(
        "LoginCode", back_populates="user", cascade="all, delete-orphan"
    )


class UserAlias(Base):
    """用户别名表"""

    __tablename__ = "user_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="aliases")


class GameGroup(Base):
    """游戏组表（组内可含多个游戏，聚合组下游戏的代肝数据）"""

    __tablename__ = "game_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    games: Mapped[list["Game"]] = relationship("Game", back_populates="group")
    group_commissions: Mapped[list["GroupCommission"]] = relationship(
        "GroupCommission", back_populates="game_group", cascade="all, delete-orphan"
    )


class GroupCommission(Base):
    """游戏组应得记录（每个用户+游戏组一行，记录该用户在组下的应得次数）"""

    __tablename__ = "group_commissions"
    __table_args__ = (
        UniqueConstraint("user_id", "game_group_id", name="uq_user_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_groups.id", ondelete="CASCADE"), nullable=False
    )
    # 该用户在该游戏组下的应得总次数
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="group_commissions")
    game_group: Mapped["GameGroup"] = relationship(
        "GameGroup", back_populates="group_commissions"
    )


class Game(Base):
    """游戏表"""

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 所属游戏组（可空 = 未分组）
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("game_groups.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    group: Mapped["GameGroup | None"] = relationship("GameGroup", back_populates="games")
    aliases: Mapped[list["GameAlias"]] = relationship(
        "GameAlias", back_populates="game", cascade="all, delete-orphan"
    )
    commissions: Mapped[list["Commission"]] = relationship(
        "Commission", back_populates="game", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="game", cascade="all, delete-orphan"
    )


class GameAlias(Base):
    """游戏别名表"""

    __tablename__ = "game_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    game: Mapped["Game"] = relationship("Game", back_populates="aliases")


class Commission(Base):
    """游戏已完成记录（每个用户+游戏一行，记录该游戏已完成次数与今日打卡）"""

    __tablename__ = "commissions"
    __table_args__ = (UniqueConstraint("user_id", "game_id", name="uq_user_game"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    # 已完成次数（由管理员通过 /代肝打卡 添加）
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 今日是否已打卡（每日重置）
    checked_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 最后一次打卡时间
    last_checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    user: Mapped["User"] = relationship("User", back_populates="commissions")
    game: Mapped["Game"] = relationship("Game", back_populates="commissions")


class Message(Base):
    """用户留言表"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="messages")
    game: Mapped["Game"] = relationship("Game", back_populates="messages")


class AuthSettings(Base):
    """管理员认证设置（只有一行，id=1）"""

    __tablename__ = "auth_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    admin_password_hash: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )
    # 管理员显示名称（登录账号仍固定为 admin）
    admin_name: Mapped[str] = mapped_column(String(64), default="admin", nullable=False)
    # 管理员上传头像文件名（位于 data/avatars/ 下）
    admin_avatar_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 管理员邮箱（二期：邮箱绑定 / 忘记密码）
    admin_email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    admin_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SecurityCounter(Base):
    """安全计数器（登录失败锁定 / 验证码 IP 限流等）"""

    __tablename__ = "security_counters"
    __table_args__ = (
        UniqueConstraint("kind", "key", name="uq_security_counter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginCode(Base):
    """用户一次性登录验证码表"""

    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 6位数字验证码
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="login_codes")


class SystemSettings(Base):
    """系统设置（单行，id=1）"""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # 是否处于反向代理模式（信任 X-Forwarded-For / X-Real-IP 获取客户端 IP）
    reverse_proxy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 是否允许用户在个人设置中上传头像
    allow_avatar_upload: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ---- 邮箱（SMTP）配置 ----
    smtp_host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(String(256), nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # none / tls(465 隐式TLS) / starttls(587)
    smtp_security: Mapped[str] = mapped_column(String(16), default="starttls", nullable=False)
    # 是否允许用户绑定邮箱 / 使用忘记密码
    allow_email_binding: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_forgot_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ---- Passkey（WebAuthn）配置 ----
    passkey_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 允许的 RP 域（JSON 数组）
    passkey_rp_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # 测试用：允许非 HTTPS 来源
    passkey_allow_http: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 定时提醒消息模板
    reminder_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- 消息渲染（文转图） ----
    render_enabled_help: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    render_enabled_list: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    render_enabled_progress: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    render_enabled_reminder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 页面模板：shadcn / apple / material / shell
    render_template: Mapped[str] = mapped_column(String(16), default="shadcn", nullable=False)
    # 自定义字体文件名（空=系统字体），族名为去掉扩展名的文件名
    render_font: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # 字体目录（默认 ./data/fonts，仅本地无头浏览器加载，不对外提供）
    render_font_dir: Mapped[str] = mapped_column(String(256), default="./data/fonts", nullable=False)


class AuditLog(Base):
    """审计日志表"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    # 操作者类型：admin / user / qq / system
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EmailCode(Base):
    """邮箱验证码表（邮箱绑定 / 忘记密码）"""

    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # bind / reset
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PasskeyCredential(Base):
    """Passkey（WebAuthn）通行密钥"""

    __tablename__ = "passkey_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 归属：普通用户绑定 user_id；管理员 user_id 为 None 且 is_admin=True
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transports: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    user: Mapped["User | None"] = relationship("User")


class OidcBinding(Base):
    """OIDC 身份绑定（第三方登录到本地账号）"""

    __tablename__ = "oidc_bindings"
    __table_args__ = (
        UniqueConstraint("provider_id", "sub", name="uq_oidc_binding"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sub: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    user: Mapped["User | None"] = relationship("User")


class ReminderSetting(Base):
    """用户定时提醒设置（每日代肝状态推送，每人一行）"""

    __tablename__ = "reminder_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    push_time: Mapped[str] = mapped_column(String(5), default="22:00", nullable=False)
    # 今日是否已发送（YYYY-MM-DD），防止重复推送
    last_sent_date: Mapped[str | None] = mapped_column(String(10), nullable=True)

    user: Mapped["User"] = relationship("User")


class DailyNote(Base):
    """用户当日备注（每日每用户一条，重置时间后视为无备注）"""

    __tablename__ = "daily_notes"
    __table_args__ = (
        UniqueConstraint("user_id", "note_date", name="uq_user_note_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    note_date: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped["User"] = relationship("User")
