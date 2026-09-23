from __future__ import annotations

from types import SimpleNamespace

from app.unified_job_workspaces_v1 import _filter_application_views


def _item(job_id: int, group: str, *, archived: bool = False):
    return SimpleNamespace(
        job_id=job_id,
        company=f"Company {job_id}",
        title=f"Role {job_id}",
        human_state=group,
        next_action="Open application",
        state_group=group,
        archived=archived,
    )


def test_active_filters_exclude_archived_and_archive_filter_is_durable_projection() -> None:
    views = [
        _item(1, "preparing"),
        _item(2, "needs_you"),
        _item(3, "needs_attention", archived=True),
        _item(4, "submitted"),
        _item(5, "applying"),
    ]

    assert [item.job_id for item in _filter_application_views(views, group="preparing")] == [1]
    assert [item.job_id for item in _filter_application_views(views, group="needs_you")] == [2]
    assert [item.job_id for item in _filter_application_views(views, group="needs_attention")] == []
    assert [item.job_id for item in _filter_application_views(views, group="archived")] == [3]
    assert [item.job_id for item in _filter_application_views(views, group="all")] == [1, 2, 4, 5]


def test_application_search_is_independent_for_multiple_jobs() -> None:
    views = [
        SimpleNamespace(
            job_id=11,
            company="Northwind",
            title="People Analyst",
            human_state="Preparing",
            next_action="No action needed",
            state_group="preparing",
            archived=False,
        ),
        SimpleNamespace(
            job_id=12,
            company="Contoso",
            title="HR Coordinator",
            human_state="Needs review",
            next_action="Review resume",
            state_group="needs_you",
            archived=False,
        ),
    ]

    assert [item.job_id for item in _filter_application_views(views, group="all", query="review resume")] == [12]
