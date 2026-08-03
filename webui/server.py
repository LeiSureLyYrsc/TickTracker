"""独立 FastAPI WebUI 服务器"""
import asyncio
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from nonebot.log import logger

from .routes.auth import router as auth_router
from .routes.admin import router as admin_router
from .routes.user import router as user_router

# 静态文件目录（Astro 构建产物）
FRONTEND_DIR = Path(__file__).parent / "frontend"


def create_app(jwt_secret: str) -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="代肝记录管理系统",
        description="Commission Tracker WebUI API",
        version="1.0.0",
    )

    # 存储 JWT 密钥到 app state
    app.state.jwt_secret = jwt_secret

    # CORS 配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册 API 路由
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(user_router)

    # 挂载静态文件
    if FRONTEND_DIR.exists():
        assets_dir = FRONTEND_DIR / "_astro"
        if assets_dir.exists():
            app.mount("/_astro", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/")
        async def serve_index():
            index = FRONTEND_DIR / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return {"message": "WebUI 未构建"}

        @app.get("/{path:path}")
        async def serve_spa(path: str):
            """SPA fallback"""
            file_path = FRONTEND_DIR / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            
            # Astro 会为每个页面生成 /xxx/index.html
            html_path = FRONTEND_DIR / path / "index.html"
            if html_path.exists():
                return FileResponse(str(html_path))

            index = FRONTEND_DIR / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return {"message": "WebUI 未构建"}
    else:
        @app.get("/")
        async def no_frontend():
            return {
                "message": "WebUI 前端未构建。请在 frontend-code/ 目录运行 npm run build",
                "api_docs": "/docs",
            }

    return app


async def start_webui_server(
    host: str, port: int, jwt_secret: str
) -> asyncio.Task:
    """后台启动服务器"""
    app = create_app(jwt_secret)

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    task = asyncio.create_task(server.serve())
    logger.info(f"[代肝追踪] WebUI 服务器启动于 http://{host}:{port}")
    return task
