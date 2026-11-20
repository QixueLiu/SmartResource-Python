"""Conservative discovery of public search-result "next page" links.

Never fabricate pagination URLs. Accept only a labeled, same-origin and same-path
link whose query retains all search parameters and advances one numeric page.
"""
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup

_PAGE_KEYS = frozenset({"page", "p", "page_no", "pageno", "pageindex", "page_index"})
_LABEL_PREFIXES = ("下一页", "下页", "后一页", "next page", "next", "›", "»", "→")
_MAX_LINKS = 1200


def _page_and_query(url: str):
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    page_values = [
        val for key, val in pairs if key.casefold() in _PAGE_KEYS
    ]
    if len(page_values) > 1:
        return None
    if page_values and not page_values[0].isdigit():
        return None
    current_page = int(page_values[0]) if page_values else 1
    regular = sorted(
        (key, val) for key, val in pairs if key.casefold() not in _PAGE_KEYS
    )
    return current_page, regular


def _next_label(element) -> bool:
    rel = element.get("rel") or []
    if isinstance(rel, str):
        rel = rel.split()
    if "next" in (x.lower() for x in rel):
        return True
    for label in (
        element.get_text(" ", strip=True),
        element.get("aria-label") or "",
        element.get("title") or "",
    ):
        value = label.strip().casefold()
        if value and any(value.startswith(prefix) for prefix in _LABEL_PREFIXES):
            return True
    return False


def discover_next_search_page(
    html: str, current_url: str, expected_host: str
) -> str | None:
    """Return a validated adjacent page URL or None if unsupported/ambiguous.

    URL validation is repeated by the fetch layer before the network request.
    """
    current = urlsplit(current_url)
    if (current.scheme not in ("https", "http") or
        current.hostname != expected_host or current.username or current.password):
        return None
    source = _page_and_query(current_url)
    if source is None:
        return None
    current_page, regular = source
    if current_page < 1 or current_page > 100000:
        return None

    soup = BeautifulSoup(html[:1_500_000], "html.parser")
    destinations = set()
    for element in soup.select("a[href], link[rel][href]")[:_MAX_LINKS]:
        if not _next_label(element):
            continue
        href = (element.get("href") or "").strip()
        if not href or len(href) > 1500:
            continue
        try:
            url = urljoin(current_url, href)
            parsed = urlsplit(url)
            if (parsed.scheme != current.scheme or
                parsed.hostname != expected_host or
                parsed.netloc.lower() != current.netloc.lower() or
                parsed.username or parsed.password or
                parsed.path != current.path):
                continue
            destination = _page_and_query(url)
            if destination is None:
                continue
            next_page, other_query = destination
            if next_page != current_page + 1 or regular != other_query:
                continue
            # Drop fragments; do not send them to a remote search endpoint.
            destinations.add(urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
            ))
        except ValueError:
            continue

    return next(iter(destinations)) if len(destinations) == 1 else None
