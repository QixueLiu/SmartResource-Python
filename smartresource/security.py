"""Allow publicly routed HTTP(S) targets only; no redirects/proxies in fetchers.

Untrusted arbitrary URL fetching remains a complex security boundary. Keep the
backend restricted to authenticated users on localhost or a private VPN.
"""
import ipaddress
import socket
from urllib.parse import urlsplit


def validate_public_url(url: str, *, resolve: bool = False) -> str:
    try:
        p = urlsplit(url.strip())
        port = p.port
    except ValueError as exc:
        raise ValueError("网址格式无效") from exc
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("仅支持无凭据的 HTTP(S) 网址")
    if port not in (None, 80, 443):
        raise ValueError("不允许非标准端口")
    host = p.hostname.lower().rstrip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("不允许本地服务地址")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        if not literal.is_global:
            raise ValueError("不允许内网或保留 IP")
    except ValueError as exc:
        if "不允许" in str(exc):
            raise
    if not host or "." not in host:
        raise ValueError("需要完整公共域名")
    if resolve:
        try:
            records = socket.getaddrinfo(host, port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("DNS 查询失败") from exc
        if not records:
            raise ValueError("DNS 查询无结果")
        if any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
            raise ValueError("网址解析到内网或保留 IP")
    return host


def validate_site(site):
    domain = validate_public_url(site.base_url)
    if site.search_url_template:
        if "{q}" not in site.search_url_template:
            raise ValueError("搜索模板必须包含 {q}")
        search_domain = validate_public_url(site.search_url_template.replace("{q}", "sample").replace("{page}", "1"))
        if search_domain != domain:
            raise ValueError("站内搜索模板必须使用相同域名")
    return domain
