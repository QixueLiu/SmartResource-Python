"""Bounded public HTML fetcher.

Explicit proxy configuration is opt-in.  Redirects are inspected but never
followed automatically, preventing silent login/homepage hops and cross-host
requests. Keep DNS/public-host validation and TLS verification enabled.
"""
import os
from urllib.parse import urljoin, urlsplit

import httpx

from .security import validate_public_url

MAX_BYTES = 3_000_000


def client_network_options():
    """Do not silently inherit proxy/CA settings from the service environment."""
    proxy = os.getenv("SMARTRESOURCE_HTTP_PROXY", "").strip() or None
    if proxy:
        try:
            parsed = urlsplit(proxy)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port == 0:
                raise ValueError("SMARTRESOURCE_HTTP_PROXY must be an HTTP(S) proxy URL")
        except ValueError as exc:
            raise ValueError("SMARTRESOURCE_HTTP_PROXY 格式无效，应为 http://127.0.0.1:端口") from exc
    return {
        "proxy": proxy,
        "trust_env": not proxy and os.getenv("SMARTRESOURCE_USE_ENV_PROXY", "0") == "1",
    }


def redirect_error(url: str, expected_host: str, status: int, location: str | None):
    if not location:
        return f"站点返回 HTTP {status} 重定向，未提供 Location"
    target = urlsplit(urljoin(url, location))
    if target.hostname != expected_host:
        return f"站点返回 HTTP {status} 跨域重定向，已拒绝"
    if target.path.rstrip("/") == "" and urlsplit(url).path.rstrip("/") != "":
        return f"站点返回 HTTP {status}，搜索/内容页跳回首页，未按搜索成功处理"
    return f"站点返回 HTTP {status} 同站重定向，需验证目标地址"


async def fetch_html(url: str, expected_host: str):
    if validate_public_url(url, resolve=True) != expected_host:
        raise ValueError("只能访问已配置的网站域名")
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(12.0),
        headers={"User-Agent": "SmartResource/0.2 (personal site search)"},
        **client_network_options(),
    ) as client:
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                raise ValueError(
                    redirect_error(url, expected_host, response.status_code,
                                   response.headers.get("location"))
                )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise ValueError("站点返回非 HTML 内容")
            chunks = bytearray()
            async for part in response.aiter_bytes():
                chunks.extend(part)
                if len(chunks) > MAX_BYTES:
                    raise ValueError("网页体积超过 3MB 限制")
            encoding = response.encoding or "utf-8"
            return chunks.decode(encoding, errors="replace")
