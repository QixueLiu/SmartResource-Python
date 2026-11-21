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

    A year, season or edition explicitly requested by the user must match
    the candidate's visible title metadata; missing metadata remains unknown,
    not a precision signal. We only apply this to explicit video sources.
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
        # When the query explicitly requests an edition, a candidate with
        # missing metadata is UNKNOWN, not a high-precision title-core match.
        # Keep the candidate available without awarding this extra boost.
        if requested is not None and found != requested:
            return False
    return True
