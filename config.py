from pydantic import BaseModel


class Config(BaseModel):
    """代肝记录管理插件配置"""

    # 数据库存储路径
    commision_db_path: str = "./data/commision_tracker.db"

    # WebUI 服务器配置
    commision_tracker_host: str = "0.0.0.0"
    commision_tracker_port: int = 8080

    # 对外访问地址（用于发给用户的登录链接）
    commision_tracker_server_url: str = "http://localhost:8080"

    # JWT 签名密钥（请务必在生产环境中修改）
    commision_tracker_jwt_secret: str = "changeme-please-use-a-strong-secret"

    # 登录验证码有效期（秒），默认 5 分钟
    commision_tracker_code_expire: int = 300

    # 每日进度重置时间（小时，24小时制），默认 04:00
    commision_tracker_reset_hour: int = 4
