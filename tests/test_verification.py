import asyncio
from smartresource.verification import choose_probe, verify_search

SITE = {"id": 1, "name": "Book demo", "base_url": "https://books.example.org",
        "kind": "novel", "search_url_template": "https://books.example.org/search?q={q}"}
HOME = '<main><a href="/book/abc">长篇小说甲</a><a href="/">首页</a></main>'


def test_choose_positive_sample():
    assert choose_probe(HOME, SITE["base_url"]) == ("长篇小说甲", "https://books.example.org/book/abc")


def test_positive_probe():
    async def fake_fetch(url, expected):
        assert expected == "books.example.org"
        assert "%E9%95%BF" in url
        return '<div><a href="/book/abc">长篇小说甲</a></div>'
    result = asyncio.run(verify_search(SITE, HOME, fake_fetch))
    assert result["status"] == "probe_passed"


def test_negative_probe_stays_candidate():
    async def fake_fetch(url, expected):
        return '<div><a href="/unrelated">长篇小说甲</a></div>'
    result = asyncio.run(verify_search(SITE, HOME, fake_fetch))
    assert result["status"] == "candidate"


def test_no_sample_does_not_fetch():
    async def fake_fetch(url, expected):
        raise AssertionError("unexpected fetch")
    result = asyncio.run(verify_search(SITE, "<a href='/'>首页</a>", fake_fetch))
    assert result["status"] == "candidate"
