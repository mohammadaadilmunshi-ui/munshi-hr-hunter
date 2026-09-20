from __future__ import annotations

import inspect

from app import api
from app import application_execution_mandate_v1 as mandate
from app import application_workspace_page as workspace


def test_initial_dashboard_has_one_customer_approve_and_submit_surface() -> None:
    source = inspect.getsource(workspace)
    assert "complete_loop_approve_submit_" in source
    assert source.count("complete_loop.approve_and_submit_for_job(") == 1
    assert "'Approve & Submit'" in source
    assert "No second review follows." in source
    assert "Do not approve again." in source


def test_customer_mandate_is_the_one_and_only_approval() -> None:
    assert "one and only customer Approve & Submit decision" in (
        mandate.approve_package.__doc__ or ""
    )


def test_late_browser_review_approval_routes_are_retired() -> None:
    source = inspect.getsource(api)
    assert '"/api/application-reviews/{review_id}/approve"' in source
    assert '"/api/application-reviews/{review_id}/approve-and-submit"' in source
    assert source.count("status.HTTP_410_GONE") >= 2
    assert "The only customer approval" in source
    assert "pre-browser" in source
