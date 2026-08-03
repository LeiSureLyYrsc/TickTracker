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
    qq_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
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
