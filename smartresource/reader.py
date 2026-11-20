"""Conservative single-page novel extraction. Returns *plain text*, never raw HTML.

Only pages belonging to an enabled user-configured site may reach this module.
No bypass of authentication, paywalls, verification challenges or DRM. No
automatic crawling of whole books. Every suggestion keeps the original URL.
"""
import re
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup, Tag
from .analyzer import detect_toc

BLOCK = re.compile(r"(chapter|read|article|book|novel|txt|content|text|正文|章节|阅读)", re.I)
NOISE = re.compile(r"(ad[-_ ]|advert|banner|recommend|comment|share|sidebar|toolbar|related|footer|header|广告|评论|推荐|推广|导航)", re.I)
NEXT = re.compile(r"^\s*(下一章|下一页|下章|next\s*(chapter|page))\s*([»›>→]*)\s*$", re.I)
PREV = re.compile(r"^\s*(上一章|上一页|上章|previous\s*(chapter|page)|prev)\s*([«‹<←]*)\s*$", re.I)
CHAPTER_NAME = re.compile(r"(第\s*[0-9一二三四五六七八九十百千零〇两]+\s*[章节回]|chapter\s*\d+)", re.I)


def _same_host_link(base: str, href: str, expected_host: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    target = urljoin(base, href)
    p = urlsplit(target)
    if p.scheme not in ("http", "https") or p.hostname != expected_host:
        return None
    if target == base:
        return None
    return target


def _clean(element: Tag):
    clone = BeautifulSoup(str(element), "html.parser")
    for tag in list(clone.select("script, style, noscript, iframe, nav, footer, header, form, button, aside, video, audio")):
        tag.decompose()
    for tag in list(clone.find_all(True)):
        if tag.name in ("a", "img", "svg"):
            tag.decompose()
            continue
        marker = " ".join(map(str, tag.get("class", []))) + " " + str(tag.get("id", ""))
        if NOISE.search(marker):
            tag.decompose()
    return clone


def _paragraphs(element: Tag) -> list[str]:
    clean = _clean(element)
    p_nodes = clean.select("p")
    lines = []
    if len(p_nodes) >= 2:
        for p in p_nodes:
            line = p.get_text(" ", strip=True)
            if len(line) >= 8:
                lines.append(line)
    if len("".join(lines)) < 120:
        for br in clean.find_all("br"):
            br.replace_with("\n")
        # Block tags get newline separators, inline text remains joined.
        for tag in clean.find_all(["p", "div", "section", "article", "h2", "h3"]):
            tag.insert_after("\n")
        text = clean.get_text("", strip=False)
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    seen = set()
    output = []
    for line in lines:
        line = re.sub(r"[ \t\u00a0]+", " ", line).strip()
        if not line or line in seen:
            continue
        if NOISE.search(line[:16]) and len(line) < 45:
            continue
        seen.add(line)
        output.append(line)
    return output[:800]


def _best_content(soup: BeautifulSoup):
    candidates = []
    nodes = soup.select("article, main, section, div")
    for node in nodes[:3500]:
        marker = (" ".join(map(str, node.get("class", []))) + " " + str(node.get("id", "")))
        if NOISE.search(marker):
            continue
        total = len(node.get_text(" ", strip=True))
        if total < 100:
            continue
        anchors = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        density = anchors / max(total, 1)
        if density > .42:
            continue
        paragraphs = _paragraphs(node)
        length = len("".join(paragraphs))
        if length < 100:
            continue
        bonus = 250 if BLOCK.search(marker) else 0
        if node.name == "article":
            bonus += 130
        if node.name == "main":
            bonus += 60
        score = min(length, 18000) + bonus + min(len(paragraphs), 30) * 9 - int(density * 500)
        # Avoid selecting a body-level container just because it wraps the entire page.
        score -= int(total > length * 2) * 150
        candidates.append((score, node, paragraphs))
    if not candidates:
        return None, []
    _, node, paragraphs = max(candidates, key=lambda item: item[0])
    return node, paragraphs


def _title(soup: BeautifulSoup) -> str:
    for query in ("h1", "article h2", ".chapter-title", "h2", "title"):
        node = soup.select_one(query)
        if node:
            name = node.get_text(" ", strip=True)
            if 2 <= len(name) <= 180:
                return name[:180]
    return "未识别章节标题"


def _nav(soup: BeautifulSoup, base: str, host: str):
    next_url, previous_url = None, None
    for a in soup.select("a[href]")[:8000]:
        name = a.get_text(" ", strip=True)[:60]
        target = _same_host_link(base, str(a.get("href") or ""), host)
        if not target:
            continue
        if not next_url and (NEXT.search(name) or a.get("rel") == ["next"]):
            next_url = target
        if not previous_url and (PREV.search(name) or a.get("rel") == ["prev"]):
            previous_url = target
    return previous_url, next_url


def _toc_items(soup: BeautifulSoup, html: str, url: str, site: dict, host: str):
    item_selector = site.get("toc_item_selector")
    link_selector = site.get("toc_link_selector")
    title_selector = site.get("toc_title_selector")
    discovered = False
    if not item_selector or not link_selector:
        suggestion = detect_toc(soup, url, host)
        if suggestion:
            item_selector = suggestion["toc_item_selector"]
            link_selector = suggestion["toc_link_selector"]
            title_selector = suggestion["toc_title_selector"]
            discovered = True
    if not item_selector or not link_selector:
        return [], discovered
    try:
        rows = soup.select(item_selector)[:1500]
        items, urls = [], set()
        for row in rows:
            link = row.select_one(link_selector)
            title = row.select_one(title_selector) if title_selector else link
            if link is None or title is None:
                continue
            href = _same_host_link(url, str(link.get("href") or ""), host)
            label = title.get_text(" ", strip=True)[:160]
            if not href or not label or href in urls:
                continue
            urls.add(href)
            items.append({"title": label, "url": href})
        return items, discovered
    except (ValueError, KeyError):
        return [], discovered


def extract_reader_page(html: str, url: str, site: dict) -> dict:
    host = urlsplit(site["base_url"]).hostname
    if urlsplit(url).hostname != host:
        raise ValueError("只能提取配置站点内的原网页")
    soup = BeautifulSoup(html, "html.parser")
    title = _title(soup)
    _, paragraphs = _best_content(soup)
    toc, toc_discovered = _toc_items(soup, html, url, site, host)

    # A book details page with a chapter index should show its directory,
    # not the surrounding book synopsis as if it were a chapter.
    is_chapter = bool(CHAPTER_NAME.search(title))
    if len(toc) >= 3 and (not is_chapter or len("".join(paragraphs)) < 300):
        return {
            "kind": "toc", "title": title, "source_url": url,
            "site_id": site["id"], "site_name": site["name"],
            "chapters": toc, "toc_discovered": toc_discovered,
            "note": "单页目录识别结果，可能缺页、缺章或混入其他链接"
        }

    content = "\n\n".join(paragraphs)
    if len(content) < 160:
        return {
            "kind": "fallback", "title": title, "source_url": url,
            "site_id": site["id"], "site_name": site["name"],
            "reason": "未能可靠区分章节正文和网站导航，请使用原网页"
        }

    previous_url, next_url = _nav(soup, url, host)
    return {
        "kind": "chapter", "title": title, "source_url": url,
        "site_id": site["id"], "site_name": site["name"],
        "content": content[:100_000], "character_count": len(content),
        "paragraph_count": len(paragraphs),
        "previous_url": previous_url, "next_url": next_url,
        "confidence": "medium" if is_chapter and len(paragraphs) >= 3 else "low",
        "note": "自动提取的纯文本；若正文缺失或包含噪声，请回到原网页"
    }
