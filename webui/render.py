"""Playwright 文转图渲染器（消息渲染成图片发送）"""
import base64
import html
from pathlib import Path

from sqlalchemy import select

from ..models import SystemSettings

TEMPLATE_IDS = ["shadcn", "apple", "material", "shell"]

# 四种页面模板（HTML + CSS）
TEMPLATES: dict[str, str] = {
    "shadcn": """body { margin:0; background:#f4f4f5; padding:24px; font-family:{font}; }
.card { background:#fff; border:1px solid #e4e4e7; border-radius:12px; padding:20px 22px;
  box-shadow:0 1px 3px rgba(0,0,0,0.08); }
.content { margin:0; white-space:pre-wrap; font-size:14px; line-height:1.75; color:#18181b;
  font-family:inherit; word-break:break-word; }""",
    "apple": """body { margin:0; background:linear-gradient(180deg,#f2f2f7,#e5e5ea); padding:24px;
  font-family:{font}; }
.card { background:rgba(255,255,255,0.9); border-radius:22px; padding:24px 26px;
  box-shadow:0 12px 40px rgba(0,0,0,0.12); }
.content { margin:0; white-space:pre-wrap; font-size:15px; line-height:1.8; color:#1c1c1e;
  font-family:inherit; word-break:break-word; }""",
    "material": """body { margin:0; background:#f6f7f9; padding:24px; font-family:{font}; }
.card { background:#fff; border-radius:16px; overflow:hidden; box-shadow:0 2px 10px rgba(0,0,0,0.1);
  border-top:6px solid #6750a4; }
.inner { padding:20px 22px; }
.content { margin:0; white-space:pre-wrap; font-size:14px; line-height:1.75; color:#1f1f1f;
  font-family:inherit; word-break:break-word; }""",
    "shell": """body { margin:0; background:#0c0c0c; padding:20px 22px; font-family:{font}; }
.content { margin:0; white-space:pre-wrap; font-size:13px; line-height:1.7; color:#4ade80;
  font-family:inherit; word-break:break-word; }
.content .dim { color:#9ca3af; }""",
}

# 共享无头浏览器（惰性启动）
_pw = None
_browser = None


async def get_render_settings(session) -> dict:
    """读取文转图相关系统设置"""
    result = await session.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    s = result.scalar_one_or_none()
    if not s:
        s = SystemSettings(id=1)
        session.add(s)
        await session.flush()
    return {
        "enabled_help": bool(s.render_enabled_help),
        "enabled_list": bool(s.render_enabled_list),
        "enabled_progress": bool(s.render_enabled_progress),
        "enabled_reminder": bool(s.render_enabled_reminder),
        "template": (s.render_template or "shadcn") if s.render_template in TEMPLATE_IDS else "shadcn",
        "font": s.render_font or "",
        "font_dir": s.render_font_dir or "./data/fonts",
    }


def list_font_files(font_dir: str) -> list[dict]:
    """列出字体目录内的字体文件（含族名=去掉扩展名的文件名）"""
    d = Path(font_dir)
    if not d.is_dir():
        return []
    fonts = []
    for ext in ("*.ttf", "*.otf", "*.woff", "*.woff2", "*.ttc"):
        for f in sorted(d.glob(ext)):
            fonts.append({"name": f.name, "family": f.stem})
    return fonts


async def _get_browser():
    """获取共享浏览器实例（惰性启动）"""
    global _pw, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    from playwright.async_api import async_playwright

    if _pw is None:
        _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch()
    return _browser


async def close_renderer() -> None:
    """关闭共享浏览器（插件关闭时调用）"""
    global _pw, _browser
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None


def _build_html(text: str, template: str, font: str, font_dir: str) -> str:
    css = TEMPLATES.get(template, TEMPLATES["shadcn"])
    font_family = "system-ui, -apple-system, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif"

    font_face = ""
    if font:
        family = Path(font).stem
        font_path = (Path(font_dir) / font).resolve()
        font_face = (
            f"@font-face {{ font-family:'{family}'; src:url('file:///{font_path.as_posix()}'); }}"
        )
        font_family = f"'{family}', {font_family}"

    css = css.replace("{font}", font_family)
    content = html.escape(text)

    card_inner = ""
    if template == "material":
        card_inner = '<div class="inner">'

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>"
        f"{font_face}{css}"
        "</style></head><body>"
        f'<div class="card">{card_inner}<pre class="content">{content}</pre>{"" if template != "material" else "</div>"}</div>'
        "</body></html>"
    )


async def render_text_to_image(
    text: str, template: str = "shadcn", font: str = "", font_dir: str = "./data/fonts"
) -> bytes | None:
    """把文本渲染为 PNG 图片；失败（未安装/无法启动浏览器）返回 None"""
    try:
        browser = await _get_browser()
        context = await browser.new_context(
            viewport={"width": 760, "height": 900},
            device_scale_factor=2,
        )
        page = await context.new_page()
        try:
            await page.set_content(_build_html(text, template, font, font_dir))
            try:
                await page.evaluate("document.fonts.ready")
            except Exception:
                pass
            img = await page.screenshot(type="png", full_page=True)
            return img
        finally:
            await context.close()
    except Exception as e:
        # 渲染失败时回退纯文本；显式记录原因，便于排查（此前静默吞掉异常）
        try:
            from nonebot.log import logger

            logger.warning(
                f"[代肝追踪] 文转图渲染失败，已回退纯文本：{type(e).__name__}: {e}"
            )
        except Exception:
            pass
        return None


async def maybe_render(text: str, kind: str, session):
    """按开关渲染消息为图片；未开启或失败返回 None（调用方回退纯文本）"""
    cfg = await get_render_settings(session)
    if not cfg[f"enabled_{kind}"]:
        return None
    img = await render_text_to_image(text, cfg["template"], cfg["font"], cfg["font_dir"])
    if not img:
        return None
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    return Message(MessageSegment.image("base64://" + base64.b64encode(img).decode()))
