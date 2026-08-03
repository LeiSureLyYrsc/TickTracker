"""JWT 认证和密码工具"""
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt


def hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希"""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def create_token(data: dict, secret: str, expires_minutes: int = 60 * 24) -> str:
    """创建 JWT token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> Optional[dict]:
    """解码 JWT token，失败返回 None"""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except JWTError:
        return None


def create_admin_token(secret: str) -> str:
    """创建管理员 JWT token"""
    return create_token({"role": "admin", "sub": "admin"}, secret)


def create_user_token(user_id: int, user_name: str, secret: str) -> str:
    """创建用户 JWT token"""
    return create_token(
        {"role": "user", "sub": str(user_id), "name": user_name}, secret
    )
