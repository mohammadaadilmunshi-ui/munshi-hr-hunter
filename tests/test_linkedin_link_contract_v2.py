from __future__ import annotations

from app import job_store
from app import jobspy_board_common
from app import linkedin_url_contract as contract
from app import product_pages
from app import product_state
from tools.runners import jobspy_runner


def test_linkedin_contract_reconstructs_numeric_job_id() -> None:
    normalized = contract.normalize_linkedin_record(
        {
            "source": "JobSpy/linkedin",
            "id": "4466640197",
            "job_url": None,
            "job_url_direct": None,
        }
    )

    assert normalized["job_url"] == "https://www.linkedin.com/jobs/view/4466640197"
    assert normalized["linkedin_job_url"] == normalized["job_url"]
    assert normalized["apply_url"] is None
    assert normalized["direct_apply_url"] is None
    assert normalized["_linkedin_link_quarantined"] is False


def test_linkedin_contract_keeps_posting_and_direct_apply_separate() -> None:
    direct = "https://boards.greenhouse.io/example/jobs/123"
    normalized = contract.normalize_linkedin_record(
        {
            "source": "JobSpy/linkedin",
            "job_url": "https://www.linkedin.com/jobs/view/example-role-4466640197/?tracking=abc",
            "job_url_direct": direct,
            "id": "4466640197",
        }
    )

    assert normalized["job_url"] == "https://www.linkedin.com/jobs/view/4466640197"
    assert normalized["linkedin_job_url"] == normalized["job_url"]
    assert normalized["apply_url"] == direct
    assert normalized["direct_apply_url"] == direct


def test_linkedin_contract_quarantines_when_posting_cannot_be_recovered() -> None:
    direct = "https://jobs.example.com/apply/abc"
    normalized = contract.normalize_linkedin_record(
        {
            "source": "JobSpy/linkedin",
            "id": "linkedin-unknown",
            "job_url": None,
            "job_url_direct": direct,
        }
    )

    assert normalized["job_url"] is None
    assert normalized["apply_url"] == direct
    assert normalized["_linkedin_link_quarantined"] is True
    assert (
        normalized["_linkedin_link_quarantine_reason"]
        == contract.LINKEDIN_QUARANTINE_REASON
    )


def test_outer_jobspy_normalizer_reconstructs_linkedin_id_without_url() -> None:
    normalized = jobspy_board_common.normalize_jobspy_row(
        {
            "id": "4466640197",
            "title": "People Analytics Specialist",
            "company": "Example Corp",
            "location": "New York, NY, US",
            "job_url": None,
            "job_url_direct": None,
            "description": "Example role",
        },
        "linkedin",
    )

    assert normalized["job_url"] == "https://www.linkedin.com/jobs/view/4466640197"
    assert normalized["apply_url"] is None
    assert normalized["_linkedin_link_quarantined"] is False


def test_jobspy_runner_reconstructs_linkedin_id_without_url() -> None:
    normalized = jobspy_runner.normalize_record(
        {
            "id": "4466640197",
            "title": "People Analytics Specialist",
            "company": "Example Corp",
            "location": "New York, NY, US",
            "job_url": None,
            "job_url_direct": None,
            "description": "Example role",
        },
        "linkedin",
    )

    assert normalized is not None
    assert normalized["job_url"] == "https://www.linkedin.com/jobs/view/4466640197"
    assert normalized["apply_url"] is None
    assert normalized["_linkedin_link_quarantined"] is False


def test_quarantine_summary_counts_linkless_as_other_targeting_reject() -> None:
    summary = {
        "run_id": "linkedin-link-run",
        "raw_jobs_found": 1,
        "raw_normalized": 1,
        "excluded_by_other_targeting": 0,
        "reject_other_targeting": 0,
        "rejected_jobs": 0,
        "primary_counts": {"REJECT_OTHER_TARGETING": 0},
        "_decision_rows": [],
        "rejection_samples": [],
        "targeting_rules_version": "test",
        "targeting_rules_hash": "hash",
    }
    quarantined = [
        {
            "source": "JobSpy/linkedin",
            "ats_job_id": "linkedin-unknown",
            "title": "People Analyst",
            "company_name": "Example Corp",
            "location_raw": "United States",
            "job_url": None,
            "direct_apply_url": "https://jobs.example.com/apply/1",
        }
    ]

    jobspy_board_common._merge_linkedin_link_quarantine(
        summary,
        quarantined,
        total_records=2,
        working_records=1,
    )

    assert summary["raw_jobs_found"] == 2
    assert summary["raw_normalized"] == 2
    assert summary["reject_other_targeting"] == 1
    assert summary["linkedin_quarantined_linkless_count"] == 1
    assert summary["linkedin_working_link_pct"] == 50.0
    assert summary["linkedin_link_health_degraded"] is True
    assert summary["_decision_rows"][-1]["primary_category"] == "REJECT_OTHER_TARGETING"
    assert (
        summary["_decision_rows"][-1]["evidence"]["reason"]
        == contract.LINKEDIN_QUARANTINE_REASON
    )


def test_persistence_backstop_marks_unrecoverable_linkedin_quarantined(
    monkeypatch,
) -> None:
    monkeypatch.setattr(job_store, "enrich_job_details", lambda value: value)
    monkeypatch.setattr(
        job_store,
        "score_job",
        lambda value: {
            "status": "found",
            "hard_rejection_reason": None,
            "target_track": None,
            "hunter_score": 0,
            "match_label": None,
            "cpt_trapdoor": 0,
            "ghost_risk_score": 0,
            "score_breakdown_json": "{}",
            "scoring_version": "test",
            "last_scored_at": "2026-09-18T00:00:00Z",
        },
    )
    monkeypatch.setattr(job_store, "apply_quality_gate", lambda value: {})
    monkeypatch.setattr(
        job_store,
        "create_universal_job_fingerprint",
        lambda value: "fingerprint",
    )
    monkeypatch.setattr(job_store, "build_manual_job_text", lambda value: "manual")

    prepared = job_store.prepare_job(
        {
            "source": "JobSpy/linkedin",
            "ats_job_id": "linkedin-unknown",
            "title": "People Analyst",
            "company_name": "Example Corp",
            "job_url": None,
            "apply_url": "https://jobs.example.com/apply/1",
        }
    )

    assert prepared["status"] == "quarantined"
    assert prepared["hard_rejection_reason"] == contract.LINKEDIN_QUARANTINE_REASON
    assert prepared["job_url"] is None
    assert prepared["apply_url"] == "https://jobs.example.com/apply/1"


def test_browse_jobs_query_fail_closes_broken_linkedin_rows(monkeypatch) -> None:
    queries: list[str] = []

    def fake_rows(sql: str, params=()):
        queries.append(sql)
        if "COUNT(*) AS count" in sql:
            return [{"count": 0}]
        return []

    monkeypatch.setattr(product_state, "_rows", fake_rows)

    rows, count = product_state.fetch_jobs()

    assert rows == []
    assert count == 0
    combined = "\n".join(queries)
    assert "NOT LIKE 'quarantined%'" in combined
    assert "linkedin.com/jobs/view/" in combined


def test_link_health_threshold_flags_below_effectively_complete() -> None:
    assert contract.linkedin_link_health_degraded(total=1000, working=999) is False
    assert contract.linkedin_link_health_degraded(total=1000, working=998) is True



class _UIContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _LinkedInCardUI:
    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []
        self.session_state: dict[str, object] = {}

    def container(self, **_kwargs):
        return _UIContext()

    def columns(self, count, **_kwargs):
        size = int(count) if isinstance(count, int) else len(count)
        return [_UIContext() for _ in range(size)]

    def markdown(self, *_args, **_kwargs):
        return None

    def button(self, *_args, **_kwargs):
        return False

    def link_button(self, label, url, **_kwargs):
        self.links.append((str(label), str(url)))
        return False


def test_normal_linkedin_job_without_direct_apply_keeps_posting_link() -> None:
    links = product_pages._external_job_links(
        {
            "source": "JobSpy/linkedin",
            "ats_job_id": "4466640888",
            "job_url": None,
            "apply_url": None,
        }
    )

    assert links == [
        (
            "Open LinkedIn job",
            "https://www.linkedin.com/jobs/view/4466640888",
            False,
        )
    ]


def test_linkedin_posting_and_direct_apply_are_both_exposed() -> None:
    links = product_pages._external_job_links(
        {
            "source": "JobSpy/linkedin",
            "ats_job_id": "4466640889",
            "job_url": "https://www.linkedin.com/jobs/view/4466640889?trk=test",
            "apply_url": "https://boards.greenhouse.io/example/jobs/456",
        }
    )

    assert links == [
        (
            "Open LinkedIn job",
            "https://www.linkedin.com/jobs/view/4466640889",
            False,
        ),
        (
            "Apply",
            "https://boards.greenhouse.io/example/jobs/456",
            True,
        ),
    ]


def test_non_linkedin_job_falls_back_to_posting_when_apply_url_missing() -> None:
    links = product_pages._external_job_links(
        {
            "source": "JobSpy/indeed",
            "job_url": "https://jobs.example.com/posting/123",
            "apply_url": None,
        }
    )

    assert links == [
        ("Open job posting", "https://jobs.example.com/posting/123", False)
    ]


def test_broken_external_urls_do_not_create_fake_link_actions() -> None:
    assert product_pages._external_job_links(
        {
            "source": "JobSpy/linkedin",
            "ats_job_id": "unknown",
            "job_url": None,
            "apply_url": "javascript:alert(1)",
        }
    ) == []


def test_cards_and_job_details_share_the_same_external_link_resolver() -> None:
    source = __import__("pathlib").Path("app/product_pages.py").read_text(encoding="utf-8")

    assert source.count("_external_job_links(row)") >= 2
    assert 'safe_link(row.get("apply_url"))' not in source[source.index("def _job_detail_dialog"):source.index("def _job_detail()")]
    assert "No application URL" not in source


def test_jobspy_linkedin_result_reaches_clickable_card_with_separate_apply(
    monkeypatch,
) -> None:
    direct = "https://boards.greenhouse.io/example/jobs/123"
    normalized = jobspy_runner.normalize_record(
        {
            "id": "4466640197",
            "title": "People Analytics Specialist",
            "company": "Example Corp",
            "location": "New York, NY, US",
            "job_url": None,
            "job_url_direct": direct,
            "description": "Example role",
        },
        "linkedin",
    )
    assert normalized is not None

    row = dict(normalized)
    row.update(
        {
            "id": 77,
            "ats_job_id": "4466640197",
            "saved": 0,
            "skipped": 0,
            "hunter_score": 90,
            "first_seen_at": "2026-09-19T12:00:00+00:00",
        }
    )

    fake_ui = _LinkedInCardUI()
    monkeypatch.setattr(product_pages, "st", fake_ui)
    monkeypatch.setattr(
        product_pages,
        "_application_flow",
        lambda _job_id: {
            "state": None,
            "display_status": None,
            "cta": "prepare",
            "cta_label": "Prepare",
        },
    )
    monkeypatch.setattr(product_pages, "pastel_for", lambda _job_id: "#ffffff")
    monkeypatch.setattr(product_pages, "score_ring", lambda *_args, **_kwargs: "")

    product_pages._job_card(row, key_prefix="acceptance")

    assert (
        "Open LinkedIn job",
        "https://www.linkedin.com/jobs/view/4466640197",
    ) in fake_ui.links
    assert ("Apply", direct) in fake_ui.links
