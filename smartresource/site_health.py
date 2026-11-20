"""Private, bounded source health observations and conservative ordering.

Only real search/reader outcomes contribute. Empty search results are neutral.
No request URL, query string, titles, cookies, HTML or exception text is stored.
Unknown sites keep a neutral prior and remain discoverable.
"""
from datetime import datetime, timezone, timedelta
import httpx
from .db import connect

OPERATIONS = {"search", "reader"}
OUTCOMES = {"success", "failure", "neutral"}
REASONS = {
    "ok", "empty_results", "heuristic_results", "missing_rules",
    "parse_failure", "timeout", "network_error", "rate_limited",
    "access_restricted", "upstream_error", "unexpected_error",
}
WINDOW_DAYS = 30
MAX_EVENTS_PER_SITE = 150


def classify_error(exc: Exception) -> str:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "rate_limited"
        if status in (401, 403, 451):
            return "access_restricted"
        if status >= 500:
            return "upstream_error"
        return "network_error"
    if isinstance(exc, (httpx.NetworkError, ConnectionError, OSError)):
        return "network_error"
    return "unexpected_error"


def observe(site_id: int, operation: str, outcome: str, reason: str) -> None:
    """Best-effort metric persistence. Reader/search must survive DB trouble."""
    if site_id < 1 or operation not in OPERATIONS or outcome not in OUTCOMES:
        return
    if reason not in REASONS:
        reason = "unexpected_error"
    try:
        with connect() as conn:
            conn.execute(
                """INSERT INTO site_health_events
                   (site_id, operation, outcome, reason, observed_at)
                   SELECT id, ?, ?, ?, ? FROM sites WHERE id=?""",
                (operation, outcome, reason,
                 datetime.now(timezone.utc).isoformat(), site_id),
            )
            conn.execute(
                """DELETE FROM site_health_events
                   WHERE site_id=? AND id NOT IN (
                     SELECT id FROM site_health_events WHERE site_id=?
                     ORDER BY id DESC LIMIT ?
                   )""",
                (site_id, site_id, MAX_EVENTS_PER_SITE),
            )
    except Exception:
        pass


def _summarize(site_id: int, rows: list[dict]) -> dict:
    successes = sum(row["outcome"] == "success" for row in rows)
    failures = sum(row["outcome"] == "failure" for row in rows)
    neutral = sum(row["outcome"] == "neutral" for row in rows)
    counted = successes + failures
    # Bayesian estimate with two imaginary successes and two failures.
    # This prevents 1 success from making a site appear "100% reliable".
    score = round(100 * (successes + 2) / (counted + 4))
    if counted < 4:
        status = "insufficient_data"
    elif score >= 65:
        status = "stable"
    elif score <= 35:
        status = "unstable"
    else:
        status = "mixed"
    return {
        "site_id": site_id,
        "score": score,
        "status": status,
        "successes": successes,
        "failures": failures,
        "neutral": neutral,
        "samples": counted,
    }


def health_map(site_ids: list[int]) -> dict[int, dict]:
    unique = list(dict.fromkeys(int(i) for i in site_ids if int(i) > 0))
    if not unique:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    result = {i: _summarize(i, []) for i in unique}
    try:
        with connect() as conn:
            # Chunk because SQLite deployments may retain small variable limits.
            for start in range(0, len(unique), 400):
                chunk = unique[start:start + 400]
                holders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT site_id, outcome FROM site_health_events
                        WHERE site_id IN ({holders}) AND observed_at >= ?""",
                    (*chunk, cutoff),
                ).fetchall()
                groups = {i: [] for i in chunk}
                for row in rows:
                    groups[row["site_id"]].append({"outcome": row["outcome"]})
                for site_id, events in groups.items():
                    result[site_id] = _summarize(site_id, events)
    except Exception:
        pass
    return result


def rank_sites(sites: list[dict], metrics: dict[int, dict] | None = None) -> list[dict]:
    if metrics is None:
        metrics = health_map([s["id"] for s in sites])
    # Unknown (50) still beats poorly performing sites, and stable sites rise.
    return sorted(sites, key=lambda s: (
        -metrics.get(s["id"], {}).get("score", 50),
        -int(bool(s.get("scan_status") == "probe_passed")),
        s["id"],
    ))


def site_health_detail(site_id: int) -> dict:
    health = health_map([site_id])[site_id]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    with connect() as conn:
        failures = conn.execute(
            """SELECT operation, reason, observed_at FROM site_health_events
               WHERE site_id=? AND outcome='failure' AND observed_at>=?
               ORDER BY id DESC LIMIT 10""", (site_id, cutoff)
        ).fetchall()
    return {**health, "window_days": WINDOW_DAYS,
            "recent_failures": [dict(r) for r in failures],
            "note": "评分只来自最近30天有限样本；未验证全站内容或版权状态"}
