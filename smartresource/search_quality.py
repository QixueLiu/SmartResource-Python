"""Conservative, fast evidence for ranking video search results.

A matching *title core* is not a verified film identity or a claim that the
URL contains playable video. Never silently discard ambiguous candidates.
"""
from .video_titles import video_title_details

_VERSION_MARKERS = (
    "video_release_year", "video_season_number", "video_edition",
)


def video_core_matches_query(item: dict, query: str) -> bool:
    """Same normalized video title after stripping only known title metadata.

    A year, season or explicit edition mismatch is a contradiction, while
    omitted metadata is unknown and not a verified match. We only apply this
    ranking hint to sources explicitly categorized as video.
    """
    if item.get("kind") != "video":
        return False
    expected = video_title_details(query)
    observed = video_title_details(str(item.get("title") or ""))
    key = expected["video_title_key"]
    if not key or observed["video_title_key"] != key:
        return False
    for marker in _VERSION_MARKERS:
        requested = expected[marker]
        found = observed[marker]
        if requested is not None and found is not None and requested != found:
            return False
    return True
