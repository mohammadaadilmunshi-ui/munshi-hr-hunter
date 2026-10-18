"""Protect existing public edge contracts through 24h cookie conversion."""
from __future__ import annotations

import pytest

from deploy.netcup.dashboard_device_cookie_caddy_v1 import patch_caddy

FIXTURE = """staging-dashboard.munshi.systems {
    encode gzip
    basic_auth {
        staging $2a$12$someOtherStagingHashNotReallyUsedHere
    }
    reverse_proxy 127.0.0.1:18501
}
dashboard.munshi.systems {
    encode zstd gzip

    # MUNSHI_PUBLIC_PRODUCTION_MAIL_INGRESS_V2
    @munshi_prod_mail path /api/mail/inbound
    @munshi_prod_tsenta_callback path /api/integrations/tsenta/oauth/callback
    @munshi_prod_status_bridge {
        method GET
        path /api/application-provider-status/by-application/*
    }
    @munshi_prod_dashboard not path /api/mail/inbound /api/integrations/tsenta/oauth/callback /api/application-provider-status/by-application/*
    reverse_proxy @munshi_prod_mail 127.0.0.1:8000
    reverse_proxy @munshi_prod_tsenta_callback 127.0.0.1:8000
    reverse_proxy @munshi_prod_status_bridge 127.0.0.1:8000
    basic_auth @munshi_prod_dashboard {
        aadil $2a$12$notActualProductionPasswordHashValue
    }

    reverse_proxy @munshi_prod_dashboard 127.0.0.1:8501
}
"""


def test_preserves_staging_existing_hash_and_public_callbacks():
    result = patch_caddy(FIXTURE)
    assert result.split("dashboard.munshi.systems {", 1)[0] == FIXTURE.split(
        "dashboard.munshi.systems {", 1
    )[0]
    assert "aadil $2a$12$notActualProductionPasswordHashValue" in result
    assert "basic_auth @munshi_prod_dashboard" not in result
    assert result.count("basic_auth {") == 2  # existing staging + enrollment
    assert "header_up X-Munshi-Auth-User {http.auth.user.id}" in result
    assert "forward_auth 127.0.0.1:8000" in result
    assert "uri /_munshi-auth/verify" in result
    assert "header_up X-Munshi-Original-URI {uri}" in result
    assert "reverse_proxy 127.0.0.1:8501" in result
    for name in (
        "@munshi_prod_mail", "@munshi_prod_tsenta_callback",
        "@munshi_prod_status_bridge",
    ):
        assert "handle " + name in result
        assert "reverse_proxy 127.0.0.1:8000" in result


@pytest.mark.parametrize("broken", [
    FIXTURE.replace("dashboard.munshi.systems {", "different.site.example {"),
    FIXTURE.replace("reverse_proxy @munshi_prod_status_bridge", "reverse_proxy @broken"),
    FIXTURE.replace("basic_auth @munshi_prod_dashboard", "basic_auth @other"),
    FIXTURE.replace("127.0.0.1:8501", "127.0.0.1:18501"),
])
def test_unknown_edge_contract_refuses_change(broken):
    with pytest.raises(ValueError):
        patch_caddy(broken)


def test_second_application_refuses_mutation():
    with pytest.raises(ValueError):
        patch_caddy(patch_caddy(FIXTURE))
