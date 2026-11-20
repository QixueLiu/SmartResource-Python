"""HTTP fetcher: no redirects, no environment proxies, size and content limits."""
import httpx
from .security import validate_public_url

MAX_BYTES = 3_000_000


async def fetch_html(url: str, expected_host: str):
    if validate_public_url(url, resolve=True) != expected_host:
        raise ValueError("只能访问已配置的网站域名")
    async with httpx.AsyncClient(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(12.0), headers={"User-Agent": "SmartResource/0.2 (personal site search)"}
    ) as client:
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                raise ValueError(f"站点返回重定向 HTTP {response.status_code}，需手动核查")
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
