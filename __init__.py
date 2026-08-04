"""代肝记录管理插件入口"""
from nonebot import get_driver, get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="commision_tracker",
    description="代肝记录管理插件 - 追踪游戏代肝次数与打卡状态，并附带WebUI",
    usage=(
        "管理员命令:\n"
        "  /代肝创建 用户/游戏 名称 [游戏组]\n"
        "  /代肝游戏组创建 名称\n"
        "  /代肝游戏组改名 旧名 新名\n"
        "  /代肝游戏组删除 名称\n"
        "  /代肝游戏移入 游戏名 游戏组\n"
        "  /代肝添加 游戏组 用户名 次数\n"
        "  /代肝次数设置 用户名 游戏组 次数/+n/-n\n"
        "  /代肝添加游戏 用户名 游戏名\n"
        "  /代肝删除组 用户名 游戏组\n"
        "  /代肝别名添加 目标 别名\n"
        "  /代肝别名删除 目标 别名\n"
        "  /代肝绑定 用户名 QQ号/@用户\n"
        "  /代肝解绑 用户名\n"
        "  /代肝打卡 游戏名 用户名 [次数]\n"
        "  /代肝重置 用户名 游戏名\n"
        "  /代肝删除 用户名 游戏名\n"
        "  /代肝管理员密码设置 密码\n"
        "\n用户命令:\n"
        "  /代肝列表\n"
        "  /进度查询\n"
        "  /代肝留言 游戏名 内容\n"
        "  /代肝登录\n"
        "  /代肝帮助"
    ),
    config=Config,
)

config = get_plugin_config(Config)
driver = get_driver()

from .handlers import admin, reminder, user  # noqa: E402, F401


@driver.on_startup
async def _on_startup():
    from nonebot.log import logger

    from .database import init_db
    from .scheduler import start_scheduler
    from .webui.server import start_webui_server

    logger.info(f"[代肝追踪] 初始化数据库: {config.commision_db_path}")
    await init_db(config.commision_db_path)

    start_scheduler(reset_hour=config.commision_tracker_reset_hour)
    
    await start_webui_server(
        host=config.commision_tracker_host,
        port=config.commision_tracker_port,
        jwt_secret=config.commision_tracker_jwt_secret,
    )


@driver.on_shutdown
async def _on_shutdown():
    from nonebot.log import logger

    from .database import close_db
    from .scheduler import stop_scheduler
    from .webui.render import close_renderer

    stop_scheduler()
    await close_renderer()
    await close_db()
    logger.info("[代肝追踪] 插件已关闭")
