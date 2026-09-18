from __future__ import annotations

from pathlib import Path

from app import application_notifications_v1 as notifications
from app import integration_credentials_v1 as integrations


def test_environment_credentials_take_precedence(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:environment-token-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")

    def should_not_read(_credential_type: str):
        raise AssertionError("vault should not be read when env value exists")

    monkeypatch.setattr(integrations, "read_secret", should_not_read)
    assert integrations.telegram_credentials() == (
        "123456789:environment-token-value",
        "-1001234567890",
    )


def test_vault_fallback_resolves_telegram_and_smtp(monkeypatch) -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "MUNSHI_SMTP2GO_API_KEY",
        "MUNSHI_SMTP2GO_SENDER",
    ):
        monkeypatch.delenv(name, raising=False)

    values = {
        integrations.TELEGRAM_BOT_TOKEN_TYPE: "123456789:vault-token-value-abcdef",
        integrations.TELEGRAM_CHAT_ID_TYPE: "-1009876543210",
        integrations.SMTP2GO_API_KEY_TYPE: "smtp2go-vault-key-abcdefghijklmnop",
        integrations.SMTP2GO_SENDER_TYPE: "notifications@munshi.systems",
    }
    monkeypatch.setattr(integrations, "read_secret", lambda kind: values.get(kind))
    assert integrations.telegram_configured() is True
    assert integrations.smtp2go_configured() is True


def test_mail_router_readiness_keeps_external_sync_separate(monkeypatch) -> None:
    monkeypatch.setattr(integrations, "read_secret", lambda _kind: None)
    monkeypatch.setattr(integrations, "secret_metadata", lambda _kind: None)
    monkeypatch.setenv("MUNSHI_MAIL_SIGNING_SECRET", "x" * 64)
    monkeypatch.delenv("MUNSHI_MAIL_EXTERNAL_SYNC_CONFIRMED", raising=False)

    readiness = integrations.integration_readiness()
    assert readiness["mail_router"]["hunter_signing_secret_configured"] is True
    assert readiness["mail_router"]["external_sync_confirmed"] is False

    monkeypatch.setenv("MUNSHI_MAIL_EXTERNAL_SYNC_CONFIRMED", "true")
    readiness = integrations.integration_readiness()
    assert readiness["mail_router"]["external_sync_confirmed"] is True


def test_smtp_adapter_accepts_vault_resolver(monkeypatch) -> None:
    monkeypatch.setattr(
        notifications,
        "smtp2go_credentials",
        lambda: (
            "smtp2go-vault-key-abcdefghijklmnop",
            "notifications@munshi.systems",
        ),
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"email_id": "smtp-vault-test-1"}}

    class Client:
        def post(self, _url, json):
            assert json["api_key"] == "smtp2go-vault-key-abcdefghijklmnop"
            assert json["sender"] == "notifications@munshi.systems"
            return Response()

    adapter = notifications.SMTP2GOAdapter(client=Client())
    result = adapter.send(
        {
            "destination_email": "candidate@example.com",
            "subject": "MUNSHI test",
            "body_text": "Verified staging notification",
        }
    )
    assert result == "smtp-vault-test-1"


def test_integration_ui_is_write_only_and_installed_last() -> None:
    root = Path(__file__).resolve().parents[1]
    ui = (root / "app" / "integration_credentials_ui_v1.py").read_text(
        encoding="utf-8"
    )
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")

    assert 'type="password"' in ui
    assert "store_secret" in ui
    assert "read_secret" not in ui
    assert "install_integration_credentials_ui(product_pages)" in shell
    assert shell.index("install_unified_job_workspaces") < shell.index(
        "install_integration_credentials_ui(product_pages)"
    )


def test_notification_worker_preserves_default_off_and_supervisor_opt_in() -> None:
    root = Path(__file__).resolve().parents[1]
    worker = (root / "scripts" / "application_notification_worker.py").read_text(
        encoding="utf-8"
    )
    supervisor = (root / "docker" / "hunter-supervisor.py").read_text(
        encoding="utf-8"
    )

    assert '"--supervised" in sys.argv[1:]' in worker
    assert "MUNSHI_APPLICATION_NOTIFICATION_WORKER_ENABLED" in worker
    assert 'scripts/application_notification_worker.py", "--supervised"' in supervisor


def test_auto_prepare_and_application_pages_explain_the_real_lifecycle() -> None:
    root = Path(__file__).resolve().parents[1]
    pages = (root / "app" / "product_pages.py").read_text(encoding="utf-8")
    applications = (root / "app" / "application_workspace_page.py").read_text(
        encoding="utf-8"
    )

    assert "How Auto Prepare works" in pages
    assert "That does not disable automation" in pages
    assert "The one-review submission gate is mandatory" in pages
    assert "Prefer review reminders during preparation" in pages

    assert "What happens to every application" in applications
    assert "You only need to intervene" in applications
    assert "**Your action now:**" in applications
    assert "There is no second approval step" in applications
