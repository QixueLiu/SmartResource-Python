"""Import Legado/阅读 JSON conservatively: public origins and plain GET/HTML only.

No source JavaScript, headers, login cookies, POST rules or signatures are run.
Imported templates are candidates until verified against a real site.
"""
import argparse
import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from .db import connect, initialize
from .models import SiteIn
from .security import validate_site, validate_public_url

FIELDS = ("name", "base_url", "kind", "enabled", "search_url_template",
          "item_selector", "title_selector", "link_selector",
          "toc_item_selector", "toc_title_selector", "toc_link_selector")


def plain_get(raw, origin):
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    bad = ("<js", "@js:", "java.", "eval(", "webview", ",{", "token=", "sign=")
    if (not value.startswith(("https://", "http://")) or len(value) > 1500
            or any(x in value.lower() for x in bad)
            or any(x in value for x in ("\n", "\r", " ", "\t"))):
        return None
    value = re.sub(r"\{\{key\}\}", "{q}", value, flags=re.I)
    value = re.sub(r"\{\{page\}\}", "{page}", value, flags=re.I)
    if "{q}" not in value or "{{" in value or "}}" in value:
        return None
    scrubbed = value.replace("{q}", "sample").replace("{page}", "1")
    if "{" in scrubbed or "}" in scrubbed:
        return None
    try:
        p, b = urlsplit(scrubbed), urlsplit(origin)
        if p.scheme != b.scheme or p.netloc.lower() != b.netloc.lower():
            return None
        validate_public_url(scrubbed)
    except ValueError:
        return None
    return value


def css(raw, role):
    if not isinstance(raw, str) or not 0 < len(raw) <= 180:
        return None
    value = raw.strip()
    if (any(x in value.lower() for x in
            ("<js", "@js:", "java.", "{{", "##", "||", "&&", "$.", "put:"))
            or "\n" in value or "\r" in value):
        return None
    parts = value.split("@")
    if role == "item" and len(parts) != 1:
        return None
    if role == "title" and len(parts) > 1 and parts[-1] in ("text", "html"):
        parts.pop()
    if role == "link" and len(parts) > 1 and parts[-1] == "href":
        parts.pop()
    if not parts or len(parts) > 3:
        return None
    selected = []
    for part in parts:
        part = part.strip()
        if part.startswith("class."):
            part = "." + part[6:]
        elif part.startswith("id."):
            part = "#" + part[3:]
        if not re.fullmatch(r"[a-zA-Z0-9_#.\-> :]+", part):
            return None
        selected.append(part)
    result = " ".join(selected)
    try:
        import soupsieve
        soupsieve.compile(result)
    except Exception:
        return None
    return result


def convert(entry):
    if not isinstance(entry, dict):
        return None
    name, url = entry.get("bookSourceName"), entry.get("bookSourceUrl")
    if not isinstance(name, str) or not name.strip() or not isinstance(url, str):
        return None
    try:
        p = urlsplit(url.strip())
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        origin = f"{p.scheme}://{p.netloc}"
        validate_public_url(origin)
    except ValueError:
        return None
    novel = entry.get("bookSourceType", 0) == 0
    template = plain_get(entry.get("searchUrl"), origin) if novel else None
    rules = entry.get("ruleSearch") if isinstance(entry.get("ruleSearch"), dict) else {}
    item = css(rules.get("bookList"), "item") if template else None
    title = css(rules.get("name"), "title") if item else None
    link = css(rules.get("bookUrl"), "link") if item else None
    if not (item and title and link):
        item = title = link = None
    site = SiteIn(
        name=name.strip()[:100], base_url=origin,
        kind="novel" if novel else "other",
        enabled=bool(entry.get("enabled", True)) and novel,
        search_url_template=template,
        item_selector=item, title_selector=title, link_selector=link,
    )
    try:
        validate_site(site)
    except ValueError:
        return None
    return site


def import_entries(entries, limit=0):
    stats = Counter()
    with connect() as conn:
        for entry in (entries[:limit] if limit else entries):
            stats["seen"] += 1
            site = convert(entry)
            if not site:
                stats["invalid"] += 1
                continue
            stats["compatible_get" if site.search_url_template else "no_get_rule"] += 1
            row = conn.execute("SELECT * FROM sites WHERE base_url=?", (site.base_url,)).fetchone()
            if row is None:
                conn.execute(
                    f"INSERT INTO sites({', '.join(FIELDS)}) VALUES({', '.join('?' for _ in FIELDS)})",
                    [int(site.enabled) if field == "enabled" else getattr(site, field)
                     for field in FIELDS],
                )
                if site.search_url_template:
                    conn.execute(
                        "UPDATE sites SET scan_status='candidate', scan_message=? "
                        "WHERE id=last_insert_rowid()",
                        ("已导入 Legado GET 模板，尚未实站验证",),
                    )
                stats["created"] += 1
            else:
                updates = {}
                # Avoid attaching CSS designed for a different search endpoint.
                compatible_css = (not row["search_url_template"]
                                  or row["search_url_template"] == site.search_url_template)
                for field in ("search_url_template", "item_selector", "title_selector", "link_selector"):
                    if field != "search_url_template" and not compatible_css:
                        continue
                    if not row[field] and getattr(site, field):
                        updates[field] = getattr(site, field)
                if row["kind"] == "other" and site.kind == "novel":
                    updates["kind"] = "novel"
                if "search_url_template" in updates:
                    updates["scan_status"] = "candidate"
                    updates["scan_message"] = "已导入 Legado GET 模板，尚未实站验证"
                if updates:
                    assignments = ", ".join(f"{field}=?" for field in updates)
                    conn.execute(
                        f"UPDATE sites SET {assignments} WHERE id=?",
                        [*updates.values(), row["id"]],
                    )
                    stats["enriched"] += 1
                else:
                    stats["unchanged"] += 1
    return stats


def scan_batch(limit):
    from .autoconfig import enqueue_scan, scan_sites
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM sites WHERE enabled=1 AND "
            "scan_status IN ('pending','candidate','needs_rule','error') "
            "ORDER BY CASE WHEN search_url_template IS NULL THEN 1 ELSE 0 END, id LIMIT ?",
            (limit,),
        ).fetchall()
    queued = enqueue_scan([r["id"] for r in rows])
    if queued:
        asyncio.run(scan_sites(queued))
    with connect() as conn:
        counts = Counter(
            r["scan_status"] for r in conn.execute(
                f"SELECT scan_status FROM sites WHERE id IN ({','.join('?' for _ in queued)})",
                queued,
            )
        ) if queued else Counter()
    counts["scanned"] = len(queued)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="一键导入阅读/Legado 书源 JSON")
    parser.add_argument("file", type=Path)
    parser.add_argument("--limit", type=int, default=0, help="处理前 N 条，0 为全部")
    parser.add_argument("--scan-limit", type=int, default=0,
                        help="导入后扫描前 N 个候选，0 不扫描（最多 100）")
    args = parser.parse_args(argv)
    if args.limit < 0 or not 0 <= args.scan_limit <= 100:
        parser.error("limit 必须 >=0，scan-limit 必须在 0-100 之间")
    source = json.loads(args.file.read_text(encoding="utf-8-sig"))
    if isinstance(source, dict):
        source = source.get("bookSources", source.get("sources"))
    if not isinstance(source, list):
        parser.error("请输入书源数组 JSON")
    initialize()
    result = import_entries(source, args.limit)
    print("导入：" + ", ".join(f"{k}={v}" for k, v in sorted(result.items())))
    print("compatible_get 仅表示格式兼容，不代表网络可达或实际可搜索")
    if args.scan_limit:
        status = scan_batch(args.scan_limit)
        print("检测：" + ", ".join(f"{k}={v}" for k, v in sorted(status.items())))


if __name__ == "__main__":
    main()
