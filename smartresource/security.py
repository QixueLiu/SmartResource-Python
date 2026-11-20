"""Basic SSRF checks for user-supplied sources. Keep server on private network."""
import ipaddress
import socket
from urllib.parse import urlsplit


def assert_public_url(url: str):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("仅允许无凭据的 http(s) 公共网址")
    if p.port not in (None, 80, 443):
        raise ValueError("非标准端口不可用于站点抓取")
    if p.hostname.lower() in ("localhost",) or p.hostname.endswith(".local"):
        raise ValueError("不允许访问本地或内网主机")
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("DNS 无结果")
    for entry in addresses:
        ip = ipaddress.ip_address(entry[4][0])
        if not ip.is_global:
            raise ValueError("不允许访问内网、保留地址或回环地址")
    return p.hostname.lower()


def validate_site(site):
    domain = assert_public_url(site.base_url)
    if site.search_url_template:
        template = site.search_url_template.replace("{q}", "test").replace("{page}", "1")
        search_domain = assert_public_url(template)
        if search_domain != domain:
            raise ValueError("站内搜索模板必须使用同一网站域名")
        if "{q}" not in site.search_url_template:
            raise ValueError("站内搜索模板必须包含 {q}")
    return domain
