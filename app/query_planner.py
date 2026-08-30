from __future__ import annotations

from typing import Any

from app.database import get_connection, get_setting
from app.targeting import configured_queries, load_rules


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stats(source_name: str) -> dict[str, dict[str, float]]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT query_name, COUNT(*) samples, SUM(request_count) requests,
                   SUM(normalized_count) normalized, SUM(eligible_count) eligible,
                   SUM(new_eligible_count) new_eligible, SUM(telegram_count) telegram,
                   SUM(error_count) errors, SUM(COALESCE(duration_ms,0)) duration_ms
            FROM query_performance WHERE source_name=? GROUP BY query_name
            """,
            (source_name,),
        ).fetchall()
        return {str(row["query_name"]): dict(row) for row in rows}
    finally:
        connection.close()


def _adaptive_score(row: dict[str, float], weights: dict[str, Any]) -> float:
    normalized = max(1.0, _number(row.get("normalized")))
    requests = max(1.0, _number(row.get("requests")))
    duration_seconds = _number(row.get("duration_ms")) / 1000.0
    new_rate = _number(row.get("new_eligible")) / normalized
    eligible_rate = _number(row.get("eligible")) / normalized
    telegram_rate = _number(row.get("telegram")) / normalized
    error_rate = _number(row.get("errors")) / requests
    runtime_per_request = duration_seconds / requests
    required = (
        "new_eligible_rate",
        "eligible_rate",
        "telegram_rate",
        "error_rate_penalty",
        "runtime_penalty",
        "runtime_penalty_reference_seconds_per_request",
    )
    missing = [key for key in required if key not in weights]
    if missing:
        raise RuntimeError("Canonical query weighting is incomplete: " + ", ".join(missing))
    reference = _number(weights["runtime_penalty_reference_seconds_per_request"])
    if reference <= 0:
        raise RuntimeError("Query runtime penalty reference must be positive.")
    return (
        new_rate * _number(weights["new_eligible_rate"])
        + eligible_rate * _number(weights["eligible_rate"])
        + telegram_rate * _number(weights["telegram_rate"])
        - error_rate * _number(weights["error_rate_penalty"])
        - min(runtime_per_request / reference, 1.0) * _number(weights["runtime_penalty"])
    )


def select_queries(source_name: str, *, limit: int | None = None, advance: bool = True) -> list[dict[str, Any]]:
    """Select configured acquisition queries without overfitting sparse runs."""
    strategy = dict(get_setting("query_strategy", {}) or {})
    candidates = configured_queries(load_rules())
    if not candidates:
        return []
    try:
        configured_maximum = int(strategy["max_queries_per_source_cycle"])
        minimum_samples = max(
            1, int(strategy["minimum_samples_before_adaptive_weighting"])
        )
        weights = dict(strategy["weights"])
        weights["runtime_penalty_reference_seconds_per_request"] = strategy[
            "runtime_penalty_reference_seconds_per_request"
        ]
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Canonical query_strategy is missing required values.") from None
    maximum = max(1, int(limit if limit is not None else configured_maximum))
    maximum = min(maximum, len(candidates))
    stats = _stats(source_name)
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT cursor FROM query_rotation_state WHERE source_name=?",
            (source_name,),
        ).fetchone()
        cursor = int(row["cursor"] or 0) if row else 0
    finally:
        connection.close()

    indexed = list(enumerate(candidates))
    under_sampled = [
        (index, query)
        for index, query in indexed
        if int(_number(stats.get(query["query"], {}).get("samples"))) < minimum_samples
    ]
    mature = [
        (index, query)
        for index, query in indexed
        if int(_number(stats.get(query["query"], {}).get("samples"))) >= minimum_samples
    ]
    under_sampled.sort(key=lambda item: ((item[0] - cursor) % len(candidates)))
    mature.sort(
        key=lambda item: (
            -_adaptive_score(stats[item[1]["query"]], weights),
            (item[0] - cursor) % len(candidates),
        )
    )
    selected_pairs = (under_sampled + mature)[:maximum]
    selected = []
    for index, query in selected_pairs:
        row = stats.get(query["query"], {})
        selected.append(
            {
                **query,
                "sample_count": int(_number(row.get("samples"))),
                "selection_mode": "exploration" if int(_number(row.get("samples"))) < minimum_samples else "adaptive",
                "adaptive_score": round(_adaptive_score(row, weights), 6),
                "configuration_source": "SQLite targeting + query_strategy",
            }
        )

    if advance and selected_pairs:
        next_cursor = (selected_pairs[-1][0] + 1) % len(candidates)
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO query_rotation_state(source_name,cursor,last_selected_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_name) DO UPDATE SET
                  cursor=excluded.cursor, last_selected_at=CURRENT_TIMESTAMP
                """,
                (source_name, next_cursor),
            )
            connection.commit()
        finally:
            connection.close()
    return selected
