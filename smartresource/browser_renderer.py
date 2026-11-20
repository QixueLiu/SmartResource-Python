"""Optional bounded Chromium fallback for explicitly configured public websites.

Enable SMARTRESOURCE_ENABLE_BROWSER=1. Browser execution is off by default.
Only run this on trusted sites: dynamic JavaScript is inherently untrusted.
"""
import os
from urllib.parse import urlsplit
from .security import validate_public_url

MAX_RENDER_BYTES = 2_500_000

def browser_enabled():
    return os.getenv("SMARTRESOURCE_ENABLE_BROWSER", "0") == "1"

async def render_html(url: str, expected_host: str) -> str:
    if not browser_enabled():
        raise RuntimeError("动态浏览器分析未启用")
    if validate_public_url(url, resolve=True) != expected_host:
        raise ValueError("动态分析只允许已配置的公共网站域名")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("需要安装 Python Playwright 和 Chromium") from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                service_workers="block", java_script_enabled=True,
                accept_downloads=False, ignore_https_errors=False,
            )
            context.set_default_timeout(12000)
            context.set_default_navigation_timeout(12000)

            async def restrict(route):
                req = route.request
                parsed = urlsplit(req.url)
                if (parsed.scheme not in ("https", "http")
                    or parsed.hostname != expected_host
                    or req.resource_type in ("image", "media", "font", "websocket")):
                    await route.abort()
                    return
                try:
                    validate_public_url(req.url, resolve=True)
                except ValueError:
                    await route.abort()
                    return
                await route.continue_()

            await context.route("**/*", restrict)
            # Route interception does not cover WebSocket handshakes.
            if hasattr(context, "route_web_socket"):
                async def block_ws(ws):
                    await ws.close()
                await context.route_web_socket("**/*", block_ws)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            await page.wait_for_timeout(650)
            if urlsplit(page.url).hostname != expected_host:
                raise ValueError("动态页面跳转到了另一个域名")
            html = await page.content()
            if len(html.encode("utf-8")) > MAX_RENDER_BYTES:
                raise ValueError("动态网页 HTML 超出限制")
            await context.close()
            return html
        finally:
            await browser.close()

async def fetch_analysis_html(url: str, expected_host: str, http_fetch=None):
    if http_fetch is None:
        from .fetcher import fetch_html
        http_fetch = fetch_html
    try:
        html = await http_fetch(url, expected_host)
    except Exception:
        if not browser_enabled():
            raise
        return await render_html(url, expected_host), "chromium"

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    has_text = len(soup.get_text(" ", strip=True)) > 160
    script_shell = (not has_text and
                    ("<script" in html.lower() or 'id="app"' in html.lower()))
    if browser_enabled() and script_shell:
        try:
            rendered = await render_html(url, expected_host)
            if len(rendered) > len(html):
                return rendered, "chromium"
        except Exception:
            pass
    return html, "http"
