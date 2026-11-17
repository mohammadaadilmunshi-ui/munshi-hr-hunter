"""Read-only, per-session mobile rerender acceleration without submission changes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import career_os_quality_patch_v1 as jobs
from app import unified_job_workspaces_v1 as applications


def _job_fetcher(calls, *, count=72):
    def fetch(*, page, page_size, include_total_count):
        calls.append((page, page_size, include_total_count))
        return ([{"id": n} for n in range(1+(page-1)*page_size,
                                             min(1+page*page_size, count+1))],
                count if include_total_count else -1)
    return fetch


def test_existing_job_batches_are_not_requeried_on_scroll_or_filter_rerun():
    session = {}
    calls = []
    fetch = _job_fetcher(calls)
    one, count = jobs._incremental_job_page_rows(
        session=session, signature="newest|all", batches=1, fetch=fetch, now=0,
    )
    assert count == 72 and len(one) == 24 and calls == [(1, 24, True)]
    two, count = jobs._incremental_job_page_rows(
        session=session, signature="newest|all", batches=2, fetch=fetch, now=2,
    )
    assert len(two) == 48
    assert calls == [(1, 24, True), (2, 24, False)]
    repeated, count = jobs._incremental_job_page_rows(
        session=session, signature="newest|all", batches=2, fetch=fetch, now=9,
    )
    assert repeated == two and len(calls) == 2


def test_job_cache_refreshes_when_filter_or_short_ttl_changes():
    session = {}
    calls = []
    fetch = _job_fetcher(calls)
    for signature, now, expected_queries in (
        ("newest", 0, 1),
        ("newest", 19.9, 1),
        ("newest", 20, 2),
        ("workable", 20.1, 3),
    ):
        rows, total = jobs._incremental_job_page_rows(
            session=session, signature=signature, batches=1, fetch=fetch, now=now,
        )
        assert len(rows) == 24 and total == 72
        assert len(calls) == expected_queries


def test_job_pages_do_not_mix_sessions_or_store_failed_queries():
    first = {}
    second = {}
    calls = []
    fetch = _job_fetcher(calls)
    for state in (first, second):
        jobs._incremental_job_page_rows(
            session=state, signature="active", batches=1, fetch=fetch, now=1,
        )
    assert len(calls) == 2
    assert first[jobs._JOB_PAGE_CACHE_KEY] is not second[jobs._JOB_PAGE_CACHE_KEY]
    def broken(**_):
        raise sqlite_error("database briefly busy")
    class sqlite_error(Exception):
        pass
    failed = {}
    with pytest.raises(sqlite_error):
        jobs._incremental_job_page_rows(
            session=failed, signature="active", batches=1, fetch=broken, now=1,
        )
    assert jobs._JOB_PAGE_CACHE_KEY not in failed


def test_incomplete_last_job_batch_stops_additional_reads():
    session = {}
    calls = []
    rows, total = jobs._incremental_job_page_rows(
        session=session, signature="all", batches=5,
        fetch=_job_fetcher(calls, count=27), now=1,
    )
    assert total == 27 and len(rows) == 27
    assert [r[0] for r in calls] == [1, 2]


def test_application_center_reuses_only_session_private_successful_projection():
    session = {}
    calls = []
    def native():
        calls.append("native")
        return [object() for _ in range(7)]
    def external():
        calls.append("external")
        return [object() for _ in range(30)]
    first, ext, cached = applications._application_center_source_snapshot(
        session=session,native_reader=native,external_reader=external,now=10,
    )
    assert not cached and len(first) == 7 and len(ext) == 30
    assert calls == ["native","external"]
    again, ext_again, cached = applications._application_center_source_snapshot(
        session=session,native_reader=native,external_reader=external,now=21.9,
    )
    assert cached and again is first and ext_again is ext
    assert calls == ["native","external"]
    applications._application_center_source_snapshot(
        session=session,native_reader=native,external_reader=external,now=22,
    )
    assert calls == ["native","external","native","external"]
    session.pop(applications._APPLICATION_CENTER_CACHE_KEY)
    applications._application_center_source_snapshot(
        session=session,native_reader=native,external_reader=external,now=23,
    )
    assert len(calls) == 6


def test_application_read_failure_does_not_cache_or_discard_last_good():
    state = {}
    original=[object()]
    def native():return original
    def unavailable():raise TimeoutError("provider unavailable")
    with pytest.raises(TimeoutError):
        applications._application_center_source_snapshot(
            session=state,native_reader=native,external_reader=unavailable,now=5,
        )
    assert state.get("_application_center_last_good") is original
    assert applications._APPLICATION_CENTER_CACHE_KEY not in state


def _view(*, group, canonical="", archived=False):
    return SimpleNamespace(
        company="Example",title="HR Analyst",human_state="Test",
        next_action="None",state_group=group,canonical_state=canonical,
        archived=archived,
    )


def test_default_active_application_filter_preserves_archive_and_interviews():
    assert applications._APPLICATION_FILTERS["Active"] == "active"
    assert list(applications._APPLICATION_FILTERS)[0] == "Active"
    values=[
        _view(group="needs_you"),_view(group="preparing"),
        _view(group="applying"),_view(group="needs_attention"),
        _view(group="submitted",canonical="INTERVIEW"),
        _view(group="submitted",canonical="ASSESSMENT"),
        _view(group="offers"),_view(group="submitted"),
        _view(group="closed"),_view(group="preparing",archived=True),
    ]
    current=applications._filter_application_views(values,group="active")
    assert len(current)==7
    assert all(not x.archived for x in current)
    assert len(applications._filter_application_views(values,group="all"))==9
    assert len(applications._filter_application_views(values,group="archived"))==1
    assert len(applications._filter_application_views(values,group="active",query="nonsense"))==0


def test_status_and_submission_authority_not_cached_in_job_page_rows():
    from pathlib import Path
    source=Path(jobs.__file__).read_text()
    pages=Path(__file__).resolve().parents[1]/"app/product_pages.py"
    cards=pages.read_text()
    assert 'pages_module._job_card(row, key_prefix="jobs", pulse_evidence=pulse_evidence)' in source
    assert 'flow = _application_flow(job_id)' in cards
    assert '_application_center_read_snapshot_v1' in cards
    assert 'st.session_state.pop("product_jobs_page_snapshot_v2", None)' in cards
