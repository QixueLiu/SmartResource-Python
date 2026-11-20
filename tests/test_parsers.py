from smartresource.novels import analyze_toc
from smartresource.search import extract_search_results


SITE = {
    "id": 1, "name": "Demo", "base_url": "https://books.example.com",
    "kind": "novel", "item_selector": ".book", "title_selector": "a.title",
    "link_selector": "a.title", "toc_item_selector": "li.chapter",
    "toc_title_selector": "a", "toc_link_selector": "a"
}


def test_search_parser_blocks_cross_host():
    rows = extract_search_results(SITE, """<div class="book"><a class="title" href="/novel/1">书 A</a></div>
    <div class="book"><a class="title" href="https://other.example.com/novel/2">坏链接</a></div>""", "https://books.example.com/search?q=test")
    assert len(rows) == 1
    assert rows[0]["title"] == "书 A"


def test_toc_gap_duplicates_and_foreign_link():
    html = """<ul><li class="chapter"><a href="/1">第1章 开始</a></li>
      <li class="chapter"><a href="/2">第2章 发展</a></li>
      <li class="chapter"><a href="/4">第4章 结局</a></li>
      <li class="chapter"><a href="/4">第4章 结局</a></li>
      <li class="chapter"><a href="https://evil.example.org/a">第5章 广告</a></li></ul>"""
    r = analyze_toc(SITE, html, "https://books.example.com/book/1")
    assert r["chapter_count"] == 3
    assert r["duplicate_entries"] == 1
    assert r["inferred_missing_count"] == 1
    assert r["missing_number_examples"] == [3]
    assert r["latest_chapter"] == "第4章 结局"
