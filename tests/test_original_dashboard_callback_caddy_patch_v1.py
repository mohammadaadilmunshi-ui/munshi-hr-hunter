from __future__ import annotations

import pytest

from deploy.netcup.original_dashboard_callback_caddy_patch import (
    BRIDGE,
    CALLBACK,
    patch_original_dashboard,
)
from tests.test_tsenta_callback_caddy_patch_v1 import FIXTURE


def test_target_only_original_domain_and_preserve_staging():
    updated = patch_original_dashboard(FIXTURE)
    assert updated.split("dashboard.munshi.systems {",1)[0] == FIXTURE.split("dashboard.munshi.systems {",1)[0]
    assert updated.count(CALLBACK) == 2
    assert updated.count(BRIDGE) == 2
    assert "@munshi_prod_tsenta_callback path " + CALLBACK in updated
    assert "reverse_proxy @munshi_prod_tsenta_callback 127.0.0.1:8000" in updated
    assert "reverse_proxy @munshi_prod_status_bridge 127.0.0.1:8000" in updated
    assert "method GET" in updated
    assert "basic_auth @munshi_prod_dashboard {" in updated
    assert "basic_auth @munshi_dashboard {" in updated
    assert "reverse_proxy @munshi_prod_dashboard 127.0.0.1:8501" in updated
    assert "reverse_proxy @munshi_dashboard 127.0.0.1:18501" in updated


@pytest.mark.parametrize("broken", [
    FIXTURE.replace("@munshi_prod_dashboard not path /api/mail/inbound","@munshi_prod_dashboard not path /"),
    FIXTURE.replace("reverse_proxy @munshi_prod_mail","reverse_proxy @missing"),
    FIXTURE.replace("basic_auth @munshi_prod_dashboard","basic_auth @missing"),
    FIXTURE.replace("dashboard.munshi.systems {","other.example {"),
])
def test_unknown_layout_is_not_patched(broken):
    with pytest.raises(ValueError):
        patch_original_dashboard(broken)


def test_running_again_requires_manual_review():
    with pytest.raises(ValueError):
        patch_original_dashboard(patch_original_dashboard(FIXTURE))
