from __future__ import annotations

from pathlib import Path

from app import telegram_listener


def test_telegram_inbound_polling_defaults_enabled(monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_TELEGRAM_POLLING_ENABLED", raising=False)
    assert telegram_listener.inbound_polling_enabled() is True


def test_telegram_inbound_polling_can_be_disabled_for_local_outbox_only(
    monkeypatch,
) -> None:
    for value in ("0", "false", "no", "off", " FALSE "):
        monkeypatch.setenv("MUNSHI_TELEGRAM_POLLING_ENABLED", value)
        assert telegram_listener.inbound_polling_enabled() is False


def test_outbox_only_mode_is_started_before_polling_application() -> None:
    source = Path("app/telegram_listener.py").read_text(encoding="utf-8")
    outbox_start = source.index("outbox_thread.start()")
    ownership_gate = source.index("if not inbound_polling_enabled():")
    polling_build = source.index("ApplicationBuilder().token(BOT_TOKEN)")

    assert outbox_start < ownership_gate < polling_build
    assert "operational outbox delivery remains active" in source
