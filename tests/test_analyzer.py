from smartresource.analyzer import analyze_html
from smartresource.search import heuristic_search_results

SITE = {"id": 9, "name": "Demo", "base_url": "https://books.example.org", "kind": "other"}


def test_form_and_chapter_list_detection():
    html = """
    <form action="/search" method="GET"><input type="hidden" name="t" value="book">
      <input name="searchkey" placeholder="搜索小说"><button>搜</button>
    </form>
    <ul id="chapter-list">
      <li><a href="/c1">第1章 开始</a></li>
      <li><a href="/c2">第2章 发展</a></li>
      <li><a href="/c3">第3章 结局</a></li>
    </ul>
    <h1>小说目录</h1>"""
    r = analyze_html(html, "https://books.example.org/book/5", SITE)
    assert r["search_candidate"]["search_url_template"] == "https://books.example.org/search?t=book&searchkey={q}"
    assert r["toc_candidate"]["toc_item_selector"] == "#chapter-list > li"
    assert r["toc_candidate"]["toc_link_selector"] == "a"
    assert r["kind_guess"] == "novel"


def test_post_form_not_inferred_and_cross_origin_toc_ignored():
    html = """
    <form method="post" action="/search"><input name="q"></form>
    <ul><li><a href="https://other.example.org/1">第1章</a></li>
    <li><a href="https://other.example.org/2">第2章</a></li>
    <li><a href="https://other.example.org/3">第3章</a></li></ul>"""
    r = analyze_html(html, "https://books.example.org", SITE)
    assert r["search_candidate"] is None
    assert r["toc_candidate"] is None
    assert any("POST" in s for s in r["notes"])


def test_keyword_search_fallback():
    html = """<a href="/book/1">蛊真人</a>
    <a href="/book/2">其他作品</a>
    <a href="https://evil.example.com/book/3">蛊真人电子版</a>"""
    rows = heuristic_search_results(SITE, html, "https://books.example.org/search?q=蛊真人", "蛊真人")
    assert len(rows) == 1
    assert rows[0]["url"] == "https://books.example.org/book/1"
    assert rows[0]["extraction"] == "heuristic"
