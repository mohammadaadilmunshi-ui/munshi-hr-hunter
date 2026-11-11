from __future__ import annotations

from app import api


def test_runtime_resolution_has_no_generic_api_secret_route() -> None:
    assert not any(
        getattr(route, "path", None)
        == "/api/application-plans/{prior_plan_id}/resolve-runtime-needs-input"
        for route in api.app.routes
    )
