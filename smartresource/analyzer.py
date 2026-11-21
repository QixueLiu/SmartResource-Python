"""Evidence-based, conservative HTML site analysis.

This module NEVER executes scripts, calls third-party aggregators or persists rules.
It infers candidates from one HTML document. All suggestions are unverified until
the user explicitly saves them and performs a real search/TOC check.
"""
import re
from collections import defaultdict
from urllib.parse import urljoin, urlsplit, urlencode
from bs4 import BeautifulSoup, Tag
from soupsieve import escape as css_escape

CHAPTER_RE = re.compile(
    r"第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回]"
    r"|\bchapter\s*[0-9]+\b", re.IGNORECASE
)
SEARCH_NAMES = ("q", "query", "keyword", "keywords", "search", "searchkey", "wd", "kw", "s", "key", "bookname", "name")


def _host_matches(url: str, host: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in ("http", "https") and parsed.hostname == host


def _selector_for_container(el: Tag) -> str:
    """Generate a simple, reviewable CSS selector, not a guaranteed unique path."""
    ident = el.get("id")
    if isinstance(ident, str) and ident.strip() and len(ident) <= 80:
        return "#" + css_escape(ident.strip())
    classes = [c for c in el.get("class", []) if isinstance(c, str) and c.strip()]
    if classes:
        return el.name + "." + css_escape(classes[0])
    if isinstance(el.parent, Tag) and el.parent.get("id"):
        return "#" + css_escape(str(el.parent["id"])) + " > " + el.name
    return el.name


def detect_search_form(soup: BeautifulSoup, page_url: str, host: str):
    hints = []
    for form in soup.select("form"):
        method = (form.get("method") or "GET").strip().upper()
        if method != "GET":
            hints.append("发现 POST 表单：需要专属适配器，未自动解析")
            continue
        action = urljoin(page_url, form.get("action") or page_url)
        if not _host_matches(action, host):
            continue
        inputs = [
            item for item in form.select("input[name]")
            if (item.get("type") or "text").lower()
            in ("text", "search", "hidden")
        ]
        search_input = next(
            (el for el in inputs if el.get("name", "").casefold() in SEARCH_NAMES
             and (el.get("type") or "text").lower() != "hidden"), None
        )
        if search_input is None:
            search_input = next((el for el in inputs if
                (el.get("type") or "text").lower() in ("text", "search")
                and any(word in (el.get("placeholder") or "").lower()
                        for word in ("搜", "书名", "搜索", "search"))), None)
        if search_input is None:
            continue
        fields = [(item.get("name"), item.get("value", "")) for item in inputs
                  if item.get("type", "").lower() == "hidden" and item.get("name")]
        fields.append((search_input["name"], "{q}"))
        sep = "&" if "?" in action else "?"
        template = action + sep + urlencode(fields).replace("%7Bq%7D", "{q}")
        return {
            "search_url_template": template,
            "confidence": "medium",
            "status": "candidate_only",
            "evidence": [
                "页面存在同域名 GET 搜索表单",
                "关键词字段：" + search_input["name"],
                "仅推断提交 URL；未验证搜索结果 CSS 或响应内容"
            ],
        }, hints
    return None, hints


def detect_toc(soup: BeautifulSoup, page_url: str, host: str):
    # Explicit EPUB navigation can use Roman numerals instead of chapter titles.
    chapter_link = 'a[href*="chapter-"]'
    for nav_selector in ('nav[epub\\:type="toc"]', 'nav[role="doc-toc"]', 'nav#toc'):
        selector = f"{nav_selector} li:has(> {chapter_link})"
        links = {
            urljoin(page_url, row["href"])
            for row in soup.select(selector + f" > {chapter_link}")[:1500]
            if _host_matches(urljoin(page_url, row["href"]), host)
        }
        if len(links) >= 3:
            return {
                "toc_item_selector": selector,
                "toc_title_selector": f":scope > {chapter_link}",
                "toc_link_selector": f":scope > {chapter_link}",
                "matched_chapter_links": len(links),
                "confidence": "medium",
                "status": "candidate_only",
                "evidence": ["在明确标注的目录导航中找到站内章节链接"],
            }
    groups = defaultdict(list)
    for a in soup.select("a[href]")[:15000]:
        title = a.get_text(" ", strip=True)
        if not title or not CHAPTER_RE.search(title[:160]):
            continue
        link = urljoin(page_url, str(a.get("href") or "").strip())
        if not _host_matches(link, host):
            continue
        row = a.find_parent("li")
        if row is None:
            row = a.parent if isinstance(a.parent, Tag) and a.parent.name in ("div", "p") else None
        if row is None or not isinstance(row.parent, Tag):
            continue
        container = row.parent
        key = (id(container), row.name)
        groups[key].append((container, row, link))
    options = []
    for values in groups.values():
        if len(values) < 3:
            continue
        container, row, _ = values[0]
        parent_selector = _selector_for_container(container)
        item_selector = parent_selector + " > " + row.name
        try:
            count = len(soup.select(item_selector))
        except Exception:
            continue
        ratio = len(values) / max(count, 1)
        if ratio < 0.4 or count > 10000:
            continue
        options.append((len(values), ratio, item_selector, len(set(v[2] for v in values))))
    if not options:
        return None
    total, ratio, selector, unique = max(options, key=lambda v: (v[0] * v[1], v[0]))
    return {
        "toc_item_selector": selector,
        "toc_title_selector": "a",
        "toc_link_selector": "a",
        "matched_chapter_links": unique,
        "confidence": "medium" if unique >= 10 and ratio >= 0.7 else "low",
        "status": "candidate_only",
        "evidence": [
            f"检测到 {total} 条章节标题模式匹配的站内链接",
            f"所在列表的章节匹配比例约 {ratio:.0%}",
            "规则只依据当前 HTML 结构猜测，未处理分页和正文"
        ],
    }


def analyze_html(html: str, page_url: str, site: dict) -> dict:
    host = urlsplit(site["base_url"]).hostname
    if not _host_matches(page_url, host):
        raise ValueError("分析网页必须属于目标网站")
    soup = BeautifulSoup(html, "html.parser")
    search, notes = detect_search_form(soup, page_url, host)
    toc = detect_toc(soup, page_url, host)
    body_text = soup.get_text(" ", strip=True)[:30000].lower()
    novel_signals = sum(word in body_text for word in ("小说", "章节", "目录", "阅读", "novel", "chapter"))
    video_signals = sum(word in body_text for word in ("视频", "播放", "电影", "电视剧", "在线观看", "episode", "watch"))
    if soup.find("video"):
        video_signals += 3
    if toc:
        novel_signals += 3
    if novel_signals >= 2 and video_signals >= 2:
        guess = "mixed"
    elif novel_signals >= 2:
        guess = "novel"
    elif video_signals >= 2:
        guess = "video"
    else:
        guess = "unknown"
    return {
        "site_id": site["id"],
        "site_name": site["name"],
        "page_url": page_url,
        "kind_guess": guess,
        "kind_confidence": "low" if guess == "unknown" or (novel_signals and video_signals) else "medium",
        "search_candidate": search,
        "toc_candidate": toc,
        "notes": notes + [
            "所有结果均为规则推断，未执行 AI 大模型和动态 JavaScript",
            "不会自动写入或覆盖已保存的规则"
        ],
    }
