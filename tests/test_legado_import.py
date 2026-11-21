"""Offline regression tests for conservative source onboarding."""
from smartresource.db import connect, initialize
from smartresource.legado_import import convert, import_entries, plain_get, css


def sample_source():
    return {
        "bookSourceName": "示例小说",
        "bookSourceUrl": "https://books.example.org/book/list",
        "bookSourceType": 0,
        "enabled": True,
        "searchUrl": "https://books.example.org/search?keyword={{key}}&page={{page}}",
        "ruleSearch": {
            "bookList": ".result",
            "name": ".title@text",
            "bookUrl": ".title@a@href",
        },
    }


def test_legado_simple_get_and_css_conversion():
    site = convert(sample_source())
    assert site is not None
    assert site.base_url == "https://books.example.org"
    assert site.search_url_template == (
        "https://books.example.org/search?keyword={q}&page={page}"
    )
    assert (site.item_selector, site.title_selector, site.link_selector) == (
        ".result", ".title", ".title a"
    )


def test_rejects_unsupported_methods_cross_domain_and_js():
    base = "https://books.example.org"
    assert plain_get("https://books.example.org/search?key={{key}}", base)
    assert plain_get("https://other.example.org/search?key={{key}}", base) is None
    assert plain_get("https://books.example.org/search?key={{key}},{\"method\":\"POST\"}", base) is None
    assert plain_get("<js>java.ajax('something')</js>", base) is None
    assert plain_get("https://books.example.org/api?token=secret&q={{key}}", base) is None
    assert css("<js>result</js>", "item") is None


def test_partial_rules_fall_back_to_heuristic():
    source = sample_source()
    source["ruleSearch"]["bookUrl"] = "@js:result"
    site = convert(source)
    assert site.search_url_template
    assert site.item_selector is None
    assert site.title_selector is None
    assert site.link_selector is None


def test_import_is_idempotent_and_preserves_manual_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "source.db"))
    initialize()
    source = sample_source()
    first = import_entries([source])
    assert first["created"] == 1
    with connect() as conn:
        site = conn.execute("SELECT * FROM sites").fetchone()
        assert site["scan_status"] == "candidate"
        conn.execute(
            "UPDATE sites SET search_url_template=?, scan_status='probe_passed'",
            ("https://books.example.org/manual?q={q}",),
        )
    second = import_entries([source])
    assert second["unchanged"] == 1
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites").fetchall()
    assert len(rows) == 1
    assert rows[0]["search_url_template"] == "https://books.example.org/manual?q={q}"
    assert rows[0]["scan_status"] == "probe_passed"


def test_non_novel_source_is_kept_but_disabled():
    src = sample_source()
    src["bookSourceType"] = 2
    site = convert(src)
    assert site is not None
    assert site.kind == "other"
    assert site.enabled is False
    assert site.search_url_template is None
