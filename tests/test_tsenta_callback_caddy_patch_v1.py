from __future__ import annotations

import pytest

from deploy.netcup.tsenta_callback_caddy_patch import CALLBACK, patch_caddy

FIXTURE = """staging-dashboard.munshi.systems {
    encode gzip
    @munshi_mail path /api/mail/inbound
    @munshi_dashboard not path /api/mail/inbound
    reverse_proxy @munshi_mail 127.0.0.1:18000
    basic_auth @munshi_dashboard {
        test $2a$12$randomlyFormattedHashedPasswordNotReal
    }
    reverse_proxy @munshi_dashboard 127.0.0.1:18501
}
dashboard.munshi.systems {
    @munshi_prod_mail path /api/mail/inbound
    @munshi_prod_dashboard not path /api/mail/inbound
    reverse_proxy @munshi_prod_mail 127.0.0.1:8000
    basic_auth @munshi_prod_dashboard {
        admin $2a$12$notRealHash
    }
    reverse_proxy @munshi_prod_dashboard 127.0.0.1:8501
}
"""


def test_only_callback_is_exempted_from_staging_basic_auth():
    updated = patch_caddy(FIXTURE)
    assert f"@munshi_tsenta_callback path {CALLBACK}" in updated
    assert f"@munshi_dashboard not path /api/mail/inbound {CALLBACK}" in updated
    assert "reverse_proxy @munshi_tsenta_callback 127.0.0.1:18000" in updated
    assert "basic_auth @munshi_dashboard {" in updated
    assert updated.rsplit("dashboard.munshi.systems {", 1)[-1] == (
        FIXTURE.rsplit("dashboard.munshi.systems {", 1)[-1]
    )
    assert "/api/jobs" not in updated


@pytest.mark.parametrize("invalid", [
    FIXTURE.replace("@munshi_dashboard not path /api/mail/inbound", "broken"),
    FIXTURE.replace("reverse_proxy @munshi_mail", "reverse_proxy @missing"),
    FIXTURE.replace("basic_auth @munshi_dashboard", "basic_auth @all"),
    FIXTURE.replace("dashboard.munshi.systems {", "dashboard.example {"),
    FIXTURE.replace("@munshi_dashboard not path /api/mail/inbound",
                    "@munshi_dashboard not path /api/mail/inbound\\n"
                    "    @munshi_tsenta_callback path /api/integrations/tsenta/oauth/callback"),
])
def test_unknown_config_fails_closed(invalid):
    with pytest.raises(ValueError):
        patch_caddy(invalid)
