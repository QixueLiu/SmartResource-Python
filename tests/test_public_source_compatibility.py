import asyncio
import json

from smartresource.reader import extract_reader_page
from smartresource.search import fetch_site_results


def test_epub_navigation_with_roman_numerals():
    page = extract_reader_page(
        """<nav id="toc"><ol>
        <li><a href="publisher.xhtml">Publisher</a></li>
        <li><a href="chapter-1.xhtml">I</a></li>
        <li><a href="chapter-2.xhtml">II</a></li>
        <li><a href="chapter-3.xhtml">III</a></li>
        <li><a href="https://elsewhere.example/chapter-4.xhtml">IV</a></li>
        </ol></nav>""",
        "https://books.example/text/",
        {"id": 1, "name": "Books", "base_url": "https://books.example"},
    )
    assert page["kind"] == "toc"
    assert [chapter["title"] for chapter in page["chapters"]] == ["I", "II", "III"]


def test_nasa_json_only_returns_video_detail_links(monkeypatch):
    async def fetch(url, host, types):
        assert url == "https://images-api.nasa.gov/search?q=Apollo+11&media_type=video&page_size=40"
        assert host == "images-api.nasa.gov"
        assert types == ("application/json",)
        items = [
            {"data": [{"title": "Apollo 11", "nasa_id": "apollo-11", "media_type": "video"}]},
            {"data": [{"title": "Apollo 11", "nasa_id": "../bad", "media_type": "video"}]},
            {"data": [{"title": "Apollo 11", "nasa_id": "photo", "media_type": "image"}]},
        ]
        return json.dumps({"collection": {"items": items}})

    monkeypatch.setattr("smartresource.search.fetch_text", fetch)
    rows, warning = asyncio.run(fetch_site_results({
        "id": 0, "name": "NASA", "kind": "video", "base_url": "https://images.nasa.gov",
        "search_url_template": "https://images.nasa.gov/search?q={q}",
    }, "Apollo 11"))
    assert warning is None
    assert [row["url"] for row in rows] == ["https://images.nasa.gov/details/apollo-11"]
