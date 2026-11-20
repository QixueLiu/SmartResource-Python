import asyncio
from smartresource import browser_renderer


def test_disabled_uses_simple_http(monkeypatch):
    monkeypatch.delenv("SMARTRESOURCE_ENABLE_BROWSER", raising=False)
    async def fake_fetch(url, host):
        return "<h1>Hello</h1>"
    html, mode = asyncio.run(browser_renderer.fetch_analysis_html(
        "https://books.example.org", "books.example.org", fake_fetch))
    assert mode == "http"
    assert "<h1>" in html


def test_dynamic_shell_invokes_renderer_when_enabled(monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_ENABLE_BROWSER", "1")
    async def fake_fetch(url, host):
        return '<html><div id="app"></div><script src="/app.js"></script></html>'
    async def fake_render(url, host):
        return '<html><div id="app"><h1>小说站</h1><form action="/search"><input name="q"></form></div></html>'
    monkeypatch.setattr(browser_renderer, "render_html", fake_render)
    html, mode = asyncio.run(browser_renderer.fetch_analysis_html(
        "https://books.example.org", "books.example.org", fake_fetch))
    assert mode == "chromium"
    assert "小说站" in html
