from __future__ import annotations

from app import autonomous_prepare_compat_v1 as compat


class FakeConnection:
    def close(self):
        return None


def test_missing_prepare_engine_preserves_historical_n8n_default(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {
            "mode": "unlimited",
            "daily_limit": None,
            "review_first": True,
        },
    )

    assert compat.configured_engine() == "N8N"


def test_explicit_native_prepare_engine_is_honored(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {
            "mode": "unlimited",
            "prepare_engine": "NATIVE",
        },
    )

    assert compat.configured_engine() == "NATIVE"


def test_explicit_n8n_prepare_engine_is_honored(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {
            "mode": "custom_limit",
            "prepare_engine": "N8N",
        },
    )

    assert compat.configured_engine() == "N8N"


def test_invalid_saved_engine_fails_closed(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {"prepare_engine": "unexpected"},
    )

    try:
        compat.configured_engine()
    except RuntimeError as error:
        assert "invalid prepare engine" in str(error)
    else:
        raise AssertionError("invalid prepare engine must fail closed")
