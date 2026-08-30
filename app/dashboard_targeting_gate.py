from __future__ import annotations

import fcntl
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from app.database import DB_PATH, get_connection, get_setting
from app.targeting import (
    CanonicalRules,
    PrimaryCategory,
    configured_queries,
    evaluate_job,
    filter_jobs,
    is_discovery_path,
    load_rules,
)


DashboardTargetingRules = CanonicalRules
MARKER = "AADIL_CANONICAL_TARGETING_V3"


def _enrich_potential_match(
    job: dict[str, Any],
    rules: DashboardTargetingRules,
) -> dict[str, Any]:
    initial = evaluate_job(job, rules)
    if not initial.get("accepted"):
        return dict(job)
    from app.job_detail import enrich_job_details

    return enrich_job_details(dict(job))


def load_dashboard_targeting_rules() -> DashboardTargetingRules:
    return load_rules()


def evaluate_dashboard_job(
    job: dict[str, Any],
    *,
    rules: DashboardTargetingRules | None = None,
    require_location: bool = True,
) -> dict[str, Any]:
    active_rules = rules or load_rules()
    # Provider payloads frequently embed required/preferred sections only
    # inside description HTML. Enrich likely matches before the one final
    # targeting decision so persistence never discovers a second policy
    # outcome after the canonical gate.
    decision = evaluate_job(_enrich_potential_match(job, active_rules), active_rules)
    if not require_location and decision["primary_category"] == PrimaryCategory.REJECT_LOCATION.value:
        # Compatibility for callers that intentionally validate role/hard/company
        # before supplying final provider location detail.
        role = decision["role_evidence"]
        hard = decision["hard_requirement_evidence"]
        company = decision.get("company_evidence")
        if not role.get("accepted"):
            return decision
        if hard.get("rejected"):
            return decision
        if isinstance(company, dict) and company.get("rejected"):
            return decision
        decision = dict(decision)
        decision["accepted"] = True
        decision["primary_category"] = PrimaryCategory.ELIGIBLE.value
        decision["reason"] = "canonical_targeting_match_location_deferred"

    compatibility_reason = {
        PrimaryCategory.REJECT_ROLE.value: "role_not_targeted",
        PrimaryCategory.REJECT_LOCATION.value: "location_not_targeted",
        PrimaryCategory.REJECT_HARD_REQUIREMENT.value: "hard_reject_keyword",
        PrimaryCategory.REJECT_COMPANY.value: "company_blacklisted",
        PrimaryCategory.REJECT_OTHER_TARGETING.value: "other_targeting_reject",
    }.get(decision["primary_category"], decision["reason"])
    output = dict(decision)
    output["canonical_reason"] = decision["reason"]
    output["reason"] = compatibility_reason
    output["location_rejection_reason"] = (
        decision["location_evidence"].get("reason")
        if decision["primary_category"] == PrimaryCategory.REJECT_LOCATION.value
        else None
    )
    output["targeting_rules_hash"] = decision["rules_hash"]
    output["strict_dashboard_targeting"] = True
    output["dashboard_targeting_gate"] = True
    return output


def filter_dashboard_jobs(
    jobs: list[dict[str, Any]],
    *,
    rules: DashboardTargetingRules | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    active_rules = rules or load_rules()
    enriched_jobs = [
        _enrich_potential_match(job, active_rules)
        for job in list(jobs)
    ]
    result = filter_jobs(enriched_jobs, active_rules)
    result["_stage_durations_ms"] = {
        "TARGET": round((time.perf_counter() - started) * 1000, 2),
    }
    return result


def adapter_gate_required(raw_job: dict[str, Any], actor: str) -> bool:
    return is_discovery_path(raw_job, actor)


def gate_adapter_job(raw_job: dict[str, Any], actor: str) -> dict[str, Any]:
    if not adapter_gate_required(raw_job, actor):
        return {
            "accepted": True,
            "primary_category": PrimaryCategory.ELIGIBLE.value,
            "reason": "verified_non_discovery_path_exempt",
            "strict_dashboard_targeting": False,
            "canonical_targeting_gate": False,
        }
    return evaluate_dashboard_job(raw_job, require_location=True)


def build_dashboard_search_queries(
    *,
    terms_per_query: int = 3,
    max_queries: int = 40,
) -> tuple[list[str], str]:
    rules = load_rules()
    configured = configured_queries(rules)
    width = max(1, min(int(terms_per_query), 6))
    queries: list[str] = []
    for index in range(0, len(configured), width):
        values = configured[index:index + width]
        queries.append(" OR ".join(f'"{item["query"]}"' for item in values))
        if len(queries) >= max(1, int(max_queries)):
            break
    return queries, rules.rules_hash


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _persist_decisions(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> None:
    for row in rows:
        connection.execute(
            """
            INSERT INTO targeting_decisions (
                run_id, source_name, external_id, job_identity, title,
                company_name, location_raw, primary_category,
                secondary_reasons_json, evidence_json, rules_version, rules_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, job_identity) DO UPDATE SET
                source_name=excluded.source_name,
                external_id=excluded.external_id,
                title=excluded.title,
                company_name=excluded.company_name,
                location_raw=excluded.location_raw,
                primary_category=excluded.primary_category,
                secondary_reasons_json=excluded.secondary_reasons_json,
                evidence_json=excluded.evidence_json,
                rules_version=excluded.rules_version,
                rules_hash=excluded.rules_hash,
                decided_at=CURRENT_TIMESTAMP
            """,
            (
                row.get("run_id"),
                row.get("source_name"),
                row.get("external_id"),
                row.get("job_identity"),
                row.get("title"),
                row.get("company_name"),
                row.get("location_raw"),
                row.get("primary_category"),
                json.dumps(row.get("secondary_reasons") or [], ensure_ascii=False),
                json.dumps(row.get("evidence") or {}, ensure_ascii=False, default=str),
                row.get("rules_version"),
                row.get("rules_hash"),
            ),
        )


def record_source_metrics(
    source_name: str,
    *,
    raw_jobs: int,
    eligible_jobs: int,
    inserted_jobs: int,
    duplicate_jobs: int,
    rejected_jobs: int | None = None,
    provider_used: str | None = None,
    filter_summary: dict[str, Any] | None = None,
) -> None:
    """Persist one exclusive, auditable source-run funnel.

    `duplicate_jobs` is the database/global duplicate count supplied by existing
    workers. Within-run duplicates come from the canonical filter summary.
    Eligible is therefore the residual final category; inserted remains the
    separately visible new-eligible count.
    """
    summary = dict(filter_summary or {})
    run_id = str(summary.get("run_id") or uuid.uuid4())
    run_started_at = str(summary.get("run_started_at") or "").strip() or None
    raw = _int(raw_jobs)
    normalized = _int(summary.get("raw_normalized") or raw)
    within_duplicates = _int(summary.get("duplicates_within_run"))
    database_duplicates = _int(duplicate_jobs)
    duplicates = within_duplicates + database_duplicates
    reject_role = _int(summary.get("reject_role") or summary.get("excluded_by_role"))
    reject_location = _int(summary.get("reject_location") or summary.get("excluded_by_location"))
    reject_hard = _int(summary.get("reject_hard_requirement") or summary.get("excluded_by_hard_reject"))
    reject_company = _int(summary.get("reject_company") or summary.get("excluded_by_company_blacklist"))
    reject_other = _int(summary.get("reject_other_targeting") or summary.get("excluded_by_other_targeting"))
    reject_total = reject_role + reject_location + reject_hard + reject_company + reject_other
    final_eligible = max(0, normalized - duplicates - reject_total)
    accounting_delta = normalized - (
        final_eligible + duplicates + reject_role + reject_location
        + reject_hard + reject_company + reject_other
    )
    inserted = _int(inserted_jobs)
    decision_rows = list(summary.pop("_decision_rows", []) or [])
    query_requests = list(summary.get("query_requests") or [])
    stage_durations = {
        str(stage).upper(): float(value)
        for stage, value in dict(summary.pop("_stage_durations_ms", {}) or {}).items()
        if value is not None
    }
    fetch_duration_ms = sum(
        max(0.0, float(item.get("duration_ms") or 0.0))
        for item in query_requests
    )
    if fetch_duration_ms > 0:
        stage_durations["FETCH"] = fetch_duration_ms
    query_new_eligible_counts = dict(summary.get("query_new_eligible_counts") or {})
    query_database_duplicate_counts = dict(summary.get("query_database_duplicate_counts") or {})
    query_telegram_counts = dict(summary.get("query_telegram_counts") or {})
    summary.pop("eligible_jobs", None)
    duration_ms = summary.get("elapsed_ms") or summary.get("duration_ms")
    request_count = _int(summary.get("request_count") or summary.get("requests"))
    if request_count == 0 and query_requests:
        request_count = sum(_int(item.get("requests")) for item in query_requests)
    telegram_count = _int(summary.get("telegram_messages") or summary.get("telegram_count"))
    downstream_success_count = _int(summary.get("downstream_success_count"))
    errors = summary.get("errors") or []
    error_count = len(errors) if isinstance(errors, list) else int(bool(errors))
    rules_hash = str(summary.get("targeting_rules_hash") or "") or None
    rules_version = str(summary.get("targeting_rules_version") or "") or None
    compact_summary = {
        key: value
        for key, value in summary.items()
        if key not in {"rejection_samples", "jobs", "raw_jobs"}
    }
    detail_json = json.dumps(compact_summary, ensure_ascii=False, default=str)[:60000]

    runtime = dict(get_setting("provider_runtime", {}) or {})
    schedule_policy = dict(runtime.get("source_schedule") or {})
    try:
        retry_attempts = int(schedule_policy["sqlite_write_retry_attempts"])
        retry_cap_seconds = float(schedule_policy["sqlite_write_retry_cap_seconds"])
        retry_base_seconds = float(schedule_policy["sqlite_write_retry_base_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "Canonical provider_runtime.source_schedule SQLite retry policy is incomplete."
        ) from None
    if retry_attempts < 1 or retry_cap_seconds < 0 or retry_base_seconds < 0:
        raise RuntimeError("Canonical source-metrics SQLite retry policy is invalid.")

    lock_dir = DB_PATH.parent / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "source_metrics.lock"
    last_error: Exception | None = None
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            for attempt in range(retry_attempts):
                connection = get_connection()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    cursor = connection.execute(
                        """
                        UPDATE source_health
                        SET jobs_found_last_run=?, raw_jobs_last_run=?,
                            normalized_jobs_last_run=?, eligible_jobs_last_run=?,
                            inserted_jobs_last_run=?, duplicate_jobs_last_run=?,
                            rejected_jobs_last_run=?, reject_role_last_run=?,
                            reject_location_last_run=?,
                            reject_hard_requirement_last_run=?,
                            reject_company_last_run=?,
                            reject_other_targeting_last_run=?,
                            accounting_delta_last_run=?, last_run_id=?,
                            request_count_last_run=?, error_count_last_run=?,
                            last_duration_ms=?, provider_used_last_run=?,
                            filter_summary_json=?, targeting_rules_hash=?,
                            health_status=CASE WHEN ?=0 THEN health_status ELSE 'degraded_accounting' END,
                            last_error=CASE WHEN ?=0 THEN last_error ELSE 'Exclusive funnel accounting delta is non-zero' END,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE source_name=?
                        """,
                        (
                            raw, raw, normalized, final_eligible, inserted, duplicates,
                            reject_total, reject_role, reject_location, reject_hard,
                            reject_company, reject_other, accounting_delta, run_id,
                            request_count, error_count, duration_ms, provider_used,
                            detail_json, rules_hash, accounting_delta, accounting_delta,
                            source_name,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            f"Source metrics rejected for unregistered source: {source_name}"
                        )
                    connection.execute(
                        """
                        INSERT INTO source_runs (
                            run_id, source_name, provider, started_at, completed_at, run_status,
                            request_count, raw_count, normalized_count, duplicate_count,
                            eligible_count, new_eligible_count, reject_role_count,
                            reject_location_count, reject_hard_requirement_count,
                            reject_company_count, reject_other_targeting_count,
                            accounting_delta, telegram_count,
                            downstream_success_count, duration_ms, error_count,
                            rules_version, rules_hash, detail_json
                        ) VALUES (
                            ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(run_id) DO UPDATE SET
                            started_at=excluded.started_at,
                            completed_at=CURRENT_TIMESTAMP,
                            run_status=excluded.run_status,
                            request_count=excluded.request_count,
                            raw_count=excluded.raw_count,
                            normalized_count=excluded.normalized_count,
                            duplicate_count=excluded.duplicate_count,
                            eligible_count=excluded.eligible_count,
                            new_eligible_count=excluded.new_eligible_count,
                            reject_role_count=excluded.reject_role_count,
                            reject_location_count=excluded.reject_location_count,
                            reject_hard_requirement_count=excluded.reject_hard_requirement_count,
                            reject_company_count=excluded.reject_company_count,
                            reject_other_targeting_count=excluded.reject_other_targeting_count,
                            accounting_delta=excluded.accounting_delta,
                            telegram_count=excluded.telegram_count,
                            downstream_success_count=excluded.downstream_success_count,
                            duration_ms=excluded.duration_ms,
                            error_count=excluded.error_count,
                            rules_version=excluded.rules_version,
                            rules_hash=excluded.rules_hash,
                            detail_json=excluded.detail_json
                        """,
                        (
                            run_id, source_name, provider_used, run_started_at,
                            "completed" if accounting_delta == 0 and not error_count else "degraded",
                            request_count, raw, normalized, duplicates, final_eligible,
                            inserted, reject_role, reject_location, reject_hard,
                            reject_company, reject_other, accounting_delta,
                            telegram_count, downstream_success_count, duration_ms,
                            error_count, rules_version, rules_hash, detail_json,
                        ),
                    )
                    stage_values = {
                        "FETCH": raw,
                        "NORMALIZE": normalized,
                        "DEDUPE": duplicates,
                        "TARGET": final_eligible,
                        "PERSIST": inserted,
                        "TELEGRAM": telegram_count,
                        "DOWNSTREAM": downstream_success_count,
                    }
                    persistence_started = time.perf_counter()
                    for stage, count in stage_values.items():
                        stage_duration = stage_durations.get(stage)
                        connection.execute(
                            """
                            INSERT INTO source_run_stages (
                              run_id, stage, item_count, duration_ms, stage_status
                            ) VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(run_id, stage) DO UPDATE SET
                                item_count=excluded.item_count,
                                duration_ms=excluded.duration_ms,
                                stage_status=excluded.stage_status,
                                recorded_at=CURRENT_TIMESTAMP
                            """,
                            (
                                run_id,
                                stage,
                                count,
                                stage_duration,
                                "degraded"
                                if accounting_delta != 0
                                else "completed"
                                if stage_duration is not None
                                else "completed_unmeasured",
                            ),
                        )
                    if decision_rows:
                        _persist_decisions(connection, decision_rows)
                    query_groups: dict[str, dict[str, Any]] = {}
                    for row in decision_rows:
                        query_name = str(row.get("query_name") or "Unattributed")
                        role_family = str(row.get("role_family") or "")
                        group = query_groups.setdefault(
                            query_name,
                            {
                                "normalized": 0,
                                "duplicate": 0,
                                "eligible": 0,
                                "role_families": set(),
                            },
                        )
                        if role_family:
                            group["role_families"].add(role_family)
                        group["normalized"] += 1
                        category = str(row.get("primary_category") or "")
                        if category == PrimaryCategory.DUPLICATE.value:
                            group["duplicate"] += 1
                        elif category == PrimaryCategory.ELIGIBLE.value:
                            group["eligible"] += 1
                    request_groups: dict[str, dict[str, Any]] = {}
                    for item in query_requests:
                        key = str(item.get("query_name") or "Unattributed")
                        group = request_groups.setdefault(
                            key,
                            {
                                "requests": 0,
                                "raw": 0,
                                "errors": 0,
                                "duration_ms": 0.0,
                                "role_families": set(),
                            },
                        )
                        role_family = str(item.get("role_family") or "")
                        if role_family:
                            group["role_families"].add(role_family)
                        group["requests"] += _int(item.get("requests"))
                        group["raw"] += _int(item.get("raw"))
                        group["errors"] += _int(item.get("errors"))
                        group["duration_ms"] += max(
                            0.0, float(item.get("duration_ms") or 0.0)
                        )
                    if (
                        "Unattributed" in query_groups
                        and len(request_groups) == 1
                        and "Unattributed" not in request_groups
                    ):
                        only_request_name = next(iter(request_groups))
                        unattributed = query_groups.pop("Unattributed")
                        target_group = query_groups.setdefault(
                            only_request_name,
                            {
                                "normalized": 0,
                                "duplicate": 0,
                                "eligible": 0,
                                "role_families": set(),
                            },
                        )
                        target_group["normalized"] += unattributed["normalized"]
                        target_group["duplicate"] += unattributed["duplicate"]
                        target_group["eligible"] += unattributed["eligible"]
                        target_group["role_families"].update(
                            unattributed["role_families"]
                        )
                    performance_query_names = set(query_groups) | set(request_groups)
                    for key in performance_query_names:
                        query_name = key
                        decisions = query_groups.get(
                            key,
                            {
                                "normalized": 0,
                                "duplicate": 0,
                                "eligible": 0,
                                "role_families": set(),
                            },
                        )
                        request_data = request_groups.get(
                            key,
                            {
                                "requests": 0,
                                "raw": 0,
                                "errors": 0,
                                "duration_ms": 0.0,
                                "role_families": set(),
                            },
                        )
                        role_families = (
                            set(decisions["role_families"])
                            | set(request_data["role_families"])
                        )
                        role_family = (
                            next(iter(role_families))
                            if len(role_families) == 1
                            else ""
                        )
                        database_dupes = _int(query_database_duplicate_counts.get(query_name))
                        if len(performance_query_names) == 1 and not query_database_duplicate_counts:
                            database_dupes = database_duplicates
                        eligible_after_global_dedupe = max(0, decisions["eligible"] - database_dupes)
                        query_new_eligible_count = _int(
                            query_new_eligible_counts.get(query_name)
                        )
                        if len(performance_query_names) == 1 and not query_new_eligible_counts:
                            query_new_eligible_count = inserted
                        query_telegram_count = _int(query_telegram_counts.get(query_name))
                        if len(performance_query_names) == 1 and not query_telegram_counts:
                            query_telegram_count = telegram_count
                        connection.execute(
                            """
                            INSERT INTO query_performance (
                              run_id, source_name, provider, query_name, role_family,
                              request_count, raw_count, normalized_count, duplicate_count,
                              eligible_count, new_eligible_count, telegram_count,
                              error_count, duration_ms
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id, source_name, provider_used, query_name, role_family,
                                request_data["requests"], request_data["raw"],
                                decisions["normalized"], decisions["duplicate"] + database_dupes,
                                eligible_after_global_dedupe,
                                query_new_eligible_count,
                                query_telegram_count,
                                request_data["errors"],
                                request_data["duration_ms"] or None,
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE source_run_stages
                        SET duration_ms=?, stage_status=CASE
                              WHEN stage_status='degraded' THEN stage_status
                              ELSE 'completed'
                            END,
                            recorded_at=CURRENT_TIMESTAMP
                        WHERE run_id=? AND stage='PERSIST'
                        """,
                        (
                            round((time.perf_counter() - persistence_started) * 1000, 2),
                            run_id,
                        ),
                    )
                    connection.commit()
                    # The operational card is created only after canonical run
                    # telemetry commits. Delivery is durable and independently
                    # retried by the shared Telegram outbox worker.
                    try:
                        from app.telegram_run_visibility import enqueue_source_run_summary

                        enqueue_source_run_summary(run_id)
                    except Exception:
                        # A reconciliation pass will recover any committed run
                        # that missed this post-commit enqueue boundary.
                        pass
                    return
                except sqlite3.OperationalError as error:
                    connection.rollback()
                    last_error = error
                    if "locked" not in str(error).casefold() or attempt == retry_attempts - 1:
                        raise
                    time.sleep(min(retry_cap_seconds, retry_base_seconds * (2 ** attempt)))
                finally:
                    connection.close()
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    if last_error is not None:
        raise last_error


def self_test() -> dict[str, Any]:
    from app.targeting import self_test as canonical_self_test

    return canonical_self_test()


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2, ensure_ascii=False))
