"""Product volume and lane tests at the canonical n8n producer boundary."""
from __future__ import annotations

import json

import pytest

from app import database, n8n_dispatch
from app.job_store import save_job
from app.product_state import create_lane, set_lane_enabled


def _seed(connection, *, title: str = "HR Operations Analyst", score: float = 99, suffix: str = "1") -> int:
    stored = save_job(connection, {
        "source": "Fixture ATS", "source_tier": 1, "ats_job_id": f"PRODUCT-{suffix}",
        "company_name": f"Fixture Employer {suffix}", "title": title,
        "location_raw": "Austin, TX", "state": "TX", "country": "US",
        "apply_url": f"https://example.test/product-{suffix}",
        "description_raw": "Support human resources operations and workforce reporting.",
        "entry_path": "adapter_discovery",
    }, actor="pytest_product")
    connection.execute("UPDATE jobs SET status='found', hunter_score=?, hard_rejection_reason=NULL, sent_to_n8n=0, already_applied=0, cpt_trapdoor=0 WHERE id=?", (score, stored["job_id"]))
    connection.commit()
    return int(stored["job_id"])


def _settings() -> None:
    database.save_setting("scoring", {"auto_n8n_threshold": 97, "daily_auto_n8n_limit": 2, "daily_manual_n8n_limit": 11}, changed_by="pytest")
    contract = json.loads((__import__("pathlib").Path(__file__).parent.parent / "config" / "downstream_contract.json").read_text())
    database.save_setting("downstream_contract", contract, changed_by="pytest")


def _plan_ids(hunter_db) -> tuple[dict, set[int]]:
    connection = database.get_connection()
    try:
        plan = n8n_dispatch.plan_candidates(connection)
    finally:
        connection.close()
    return plan, {int(job["id"]) for job in plan["auto_candidates"]}


def test_absent_product_policy_preserves_legacy_auto_limit(hunter_db) -> None:
    _settings()
    connection = database.get_connection()
    try:
        _seed(connection, suffix="legacy-a"); _seed(connection, suffix="legacy-b"); _seed(connection, suffix="legacy-c")
    finally: connection.close()
    plan, candidate_ids = _plan_ids(hunter_db)
    assert plan["product_volume_mode"] == "legacy"
    assert plan["auto_limit"] == 2
    assert len(candidate_ids) == 2


def test_unlimited_only_removes_product_quota_not_canonical_gates(hunter_db) -> None:
    _settings(); database.save_setting("product_automation_policy_v1", {"mode": "unlimited"}, changed_by="pytest")
    connection = database.get_connection()
    try:
        allowed = _seed(connection, suffix="unlimited-ok")
        blocked = _seed(connection, title="Senior HR Operations Analyst", suffix="unlimited-blocked")
    finally: connection.close()
    plan, candidate_ids = _plan_ids(hunter_db)
    assert plan["auto_limit"] is None
    assert allowed in candidate_ids
    assert blocked not in candidate_ids


def test_custom_limit_paused_and_pause_after_batch_are_producer_enforced(hunter_db) -> None:
    _settings()
    connection = database.get_connection()
    try:
        _seed(connection, suffix="limit-a"); _seed(connection, suffix="limit-b")
    finally: connection.close()
    database.save_setting("product_automation_policy_v1", {"mode": "custom_limit", "daily_limit": 1}, changed_by="pytest")
    plan, candidates = _plan_ids(hunter_db)
    assert plan["auto_limit"] == 1 and len(candidates) == 1
    for mode in ("paused", "pause_after_batch"):
        database.save_setting("product_automation_policy_v1", {"mode": mode}, changed_by="pytest")
        plan, candidates = _plan_ids(hunter_db)
        assert plan["auto_limit"] == 0 and candidates == set()
    connection = database.get_connection()
    try:
        queued_job = dict(connection.execute("SELECT * FROM jobs ORDER BY id LIMIT 1").fetchone())
        n8n_dispatch.insert_queue_item(connection, queued_job, "auto_top_match", "production")
        connection.commit()
    finally: connection.close()
    plan, candidates = _plan_ids(hunter_db)
    assert candidates == set(), "pause-after-batch must create no new automatic work"
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM n8n_dispatch_queue").fetchone()[0] == 1
    finally: connection.close()


def test_manual_limit_is_unaffected_and_invalid_product_policy_fails_closed(hunter_db) -> None:
    _settings(); database.save_setting("product_automation_policy_v1", {"mode": "paused"}, changed_by="pytest")
    connection = database.get_connection()
    try:
        plan = n8n_dispatch.plan_candidates(connection)
    finally: connection.close()
    assert plan["manual_limit"] == 11
    database.save_setting("product_automation_policy_v1", {"mode": "custom_limit", "daily_limit": 0}, changed_by="pytest")
    connection = database.get_connection()
    try:
        with pytest.raises(RuntimeError, match="invalid daily limit"):
            n8n_dispatch.plan_candidates(connection)
    finally: connection.close()


def test_enabled_lanes_narrow_only_and_apply_maximum_score(hunter_db) -> None:
    _settings()
    connection = database.get_connection()
    try:
        match = _seed(connection, title="HR Operations Analyst", score=98, suffix="lane-match")
        other = _seed(connection, title="People Analytics Analyst", score=99, suffix="lane-other")
    finally: connection.close()
    create_lane("Operations", {"keywords": "operations,\npeople ops"}, 99, "unlimited", None)
    lane_id = __import__("app.product_state", fromlist=["lanes"]).lanes()[0]["id"]
    set_lane_enabled(lane_id, True)
    _plan, candidates = _plan_ids(hunter_db)
    assert candidates == set(), "matching lane minimum score must narrow the canonical threshold"
    connection = database.get_connection()
    try: connection.execute("UPDATE jobs SET hunter_score=99 WHERE id=?", (match,)); connection.commit()
    finally: connection.close()
    _plan, candidates = _plan_ids(hunter_db)
    assert candidates == {match}
    assert other not in candidates


def test_disabled_lanes_have_zero_effect(hunter_db) -> None:
    _settings()
    connection = database.get_connection()
    try:
        first = _seed(connection, title="HR Operations Analyst", suffix="disabled-a")
        second = _seed(connection, title="People Analytics Analyst", suffix="disabled-b")
    finally: connection.close()
    create_lane("Disabled unrelated", {"keywords": "recruiting"}, 100, "unlimited", None)
    _plan, candidates = _plan_ids(hunter_db)
    assert candidates == {first, second}
