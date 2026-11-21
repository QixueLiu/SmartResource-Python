import asyncio

from smartresource import search


def test_full_search_cursor_reaches_every_configured_source(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "cursor.sqlite"))
    attempted = []

    async def fetch(site, keyword):
        attempted.append(site["id"])
        return [], None

    monkeypatch.setattr(search, "fetch_site_results", fetch)
    sites = [
        {"id": number, "name": str(number), "kind": "novel",
         "base_url": "https://books.example",
         "search_url_template": "https://books.example/search?q={q}" if number != 55 else None}
        for number in range(1, 132)
    ]

    async def run():
        cursor = 0
        sizes = []
        while cursor is not None:
            result = await search.search_all(
                list(reversed(sites)), "test", mode="full", kind="novel",
                after_site_id=cursor,
            )
            assert result["searchable_sites"] == 130
            assert result["unconfigured_sites"] == 1
            sizes.append(result["searched_sites"])
            cursor = result["next_site_id"]
        assert sizes == [48, 48, 34]

    asyncio.run(run())
    assert sorted(attempted) == [number for number in range(1, 132) if number != 55]
