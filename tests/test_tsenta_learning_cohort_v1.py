from __future__ import annotations

import json

from app import tsenta_learning_cohort_v1 as cohort
from app import tsenta_observation_v1 as observations


def _rows(total: int, *, platforms: tuple[str, ...] = ("workday", "icims", "oracle")):
    rows = []
    for i in range(total):
        ats = platforms[i % len(platforms)]
        application_id = f"synthetic-application-{i}"
        status = observations.observation_from_status(
            provider_application_id=application_id,
            ats_type=ats,
            status={"status": "COMPLETED", "isTerminal": True},
            observed_at=f"2026-10-16T00:{i:02}:00+00:00",
        )
        fields = observations.observations_from_review(
            provider_application_id=application_id,
            ats_type=ats,
            observed_at=f"2026-10-16T00:{i:02}:00+00:00",
            review={"awaitingReview": True, "fields": [
                {"label": "State / Province", "kind": "text",
                 "required": True, "value": None}
            ]},
        )
        rows.extend(
            [
                dict(
                    row,
                    observation_id=f"status-{i}" if row is status else f"field-{i}",
                    observed_json=json.dumps(row),
                )
                for row in (status, *fields)
            ]
        )
    return rows


def test_cohorts_count_distinct_terminal_applications_not_fields():
    rows = _rows(9)
    # Repeated forms, even with many fields, cannot inflate process count.
    for index in range(24):
        rows.append(dict(rows[1], observation_id=f"duplicate-{index}"))
    before = cohort.summarize_rows(rows)
    assert before["phase"] == "COLLECTING"
    assert before["processed_applications_total"] == 9
    ready = cohort.summarize_rows(_rows(10))
    assert ready["phase"] == "EVALUATION_READY"
    assert ready["cohort_processed"] == 10
    assert ready["unique_ats_in_cohort"] == 3
    assert ready["auto_upgrade_enabled"] is False
    assert ready["independent_employer_receipts_verified"] is False
    assert len(ready["shadow_improvement_candidates"]) > 0
    assert all(x["deployment_state"] == "SHADOW_ONLY"
               for x in ready["shadow_improvement_candidates"])
    complete = cohort.summarize_rows(_rows(15))
    assert complete["phase"] == "COHORT_COMPLETE"
    assert complete["cohort_number"] == 1
    next_cycle = cohort.summarize_rows(_rows(16))
    assert next_cycle["phase"] == "COLLECTING"
    assert next_cycle["cohort_number"] == 2
    assert next_cycle["cohort_processed"] == 1


def test_a_newer_nonterminal_status_removes_application_from_cohort():
    rows = _rows(10, platforms=("greenhouse",))
    old = rows[0]
    rows.append(dict(old, observation_id="later-reopened",
                     observed_at="2026-10-17T10:00:00+00:00",
                     observed_json=json.dumps({
                         "provider_status":"PAUSED",
                         "is_terminal":False,
                     })))
    result = cohort.summarize_rows(rows)
    assert result["processed_applications_total"] == 9
    assert result["phase"] == "COLLECTING"


def test_unknown_ats_is_dynamic_not_platform_whitelisted():
    rows = _rows(10, platforms=("future-ats-z",))
    snap = cohort.summarize_rows(rows)
    assert snap["ats_coverage"] == {"future-ats-z": 10}
    assert snap["unique_ats_in_cohort"] == 1
    assert snap["phase"] == "EVALUATION_READY"
    assert snap["shadow_improvement_candidates"][0]["ats_type"] == "future-ats-z"
    rendered = json.dumps(snap)
    assert "synthetic-application-" not in rendered
    assert "State / Province" not in rendered


def test_no_status_never_counts_as_processed():
    rows = _rows(15)
    only_fields = [row for row in rows if row["observation_kind"] == "FORM_FIELD"]
    assert cohort.summarize_rows(only_fields)["processed_applications_total"] == 0


def test_database_snapshot_tenant_scoped_idempotent_and_readonly(hunter_db):
    del hunter_db
    from app.database import get_connection

    rows = []
    for i in range(10):
        rows.append(observations.observation_from_status(
            provider_application_id=f"provider-private-{i}",
            ats_type=("ashby", "successfactors")[i % 2],
            status={"status": "FAILED", "isTerminal": True},
        ))
        rows.extend(observations.observations_from_review(
            provider_application_id=f"provider-private-{i}",
            ats_type=("ashby", "successfactors")[i % 2],
            review={"awaitingReview": True, "fields": [
                {"label": "Region", "kind": "text", "required": True, "value": None},
            ]},
        ))
    first = observations.persist_observations(rows)
    assert first["inserted"] == 20
    assert observations.persist_observations(rows)["replayed"] == 20
    with get_connection() as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) FROM tsenta_observations_v1").fetchone()[0]
    first_snap = cohort.learning_cohort_snapshot()
    assert first_snap["phase"] == "EVALUATION_READY"
    assert first_snap["unique_ats_in_cohort"] == 2
    assert cohort.learning_cohort_snapshot() == first_snap
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tsenta_observations_v1").fetchone()[0] == count_before
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert "provider-private" not in json.dumps(first_snap)


def test_unmigrated_db_learning_snapshot_installs_no_tables(hunter_db):
    del hunter_db
    from app.database import get_connection

    with get_connection() as conn:
        before = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    snap = cohort.learning_cohort_snapshot()
    assert snap["processed_applications_total"] == 0
    with get_connection() as conn:
        after = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert after == before




def test_replayed_terminal_status_can_follow_a_newer_paused_status(hunter_db):
    del hunter_db
    base = {
        "provider_application_id": "same-application-again",
        "ats_type": "generic_new_ats",
    }
    first = observations.observation_from_status(
        **base, status={"status": "COMPLETE", "isTerminal": True},
        observed_at="2026-10-16T00:00:00+00:00",
    )
    paused = observations.observation_from_status(
        **base, status={"status": "PAUSED", "isTerminal": False},
        observed_at="2026-10-16T00:01:00+00:00",
    )
    again = observations.observation_from_status(
        **base, status={"status": "COMPLETE", "isTerminal": True},
        observed_at="2026-10-16T00:02:00+00:00",
    )
    assert observations.persist_observations([first])["inserted"] == 1
    assert cohort.learning_cohort_snapshot()["processed_applications_total"] == 1
    assert observations.persist_observations([paused])["inserted"] == 1
    assert cohort.learning_cohort_snapshot()["processed_applications_total"] == 0
    assert observations.persist_observations([again]) == {
        "inserted": 0, "replayed": 1,
    }
    assert cohort.learning_cohort_snapshot()["processed_applications_total"] == 1



def test_more_than_five_thousand_old_field_rows_does_not_reset_cohort(hunter_db):
    del hunter_db
    from app.database import get_connection

    observation = observations.observation_from_status(
        provider_application_id="valuable-terminal",
        ats_type="yet-another-ats",
        status={"status": "COMPLETED", "isTerminal": True},
    )
    observations.persist_observations([observation])
    with get_connection() as conn:
        owner = conn.execute(
            "SELECT tenant_id,user_id FROM tsenta_observations_v1 LIMIT 1"
        ).fetchone()
        conn.executemany(
            """INSERT INTO tsenta_observations_v1(
                observation_id,tenant_id,user_id,provider_application_ref_sha256,
                ats_type,observation_kind,evidence_grade,observed_json,observed_at
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                (
                    f"stale-noise-{i}", owner["tenant_id"], owner["user_id"],
                    "e" * 64, "old_vendor", "FORM_FIELD", "OBSERVED", "{}",
                    "2026-10-01T00:00:00+00:00",
                )
                for i in range(5100)
            ),
        )
        conn.commit()
    snap = cohort.learning_cohort_snapshot()
    assert snap["processed_applications_total"] == 1
    assert snap["cohort_processed"] == 1
    assert snap["ats_coverage"] == {"yet-another-ats": 1}
    assert snap["phase"] == "COLLECTING"
