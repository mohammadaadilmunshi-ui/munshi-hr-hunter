from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import application_plan_delivery_v2 as delivery


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 202) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _signed() -> SimpleNamespace:
    body = json.dumps(
        {
            "handoff_id": "plan-handoff-delivery-1",
            "plan_id": "application-plan-delivery-1",
            "plan_digest": "a" * 64,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return SimpleNamespace(
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Munshi-Event-Id": "plan-handoff-delivery-1",
            "X-Munshi-Timestamp": "1000",
            "X-Munshi-Content-SHA256": "b" * 64,
            "X-Munshi-Signature": "sha256=" + ("c" * 64),
        },
    )


def test_delivery_is_default_off_and_requires_private_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(delivery.transport.LIVE_HANDOFF_ENV, raising=False)
    monkeypatch.delenv(delivery.APPLY_BASE_URL_ENV, raising=False)

    with pytest.raises(RuntimeError, match="live handoff is disabled"):
        delivery.deliver_application_plan(
            plan_id="application-plan-delivery-1",
            idempotency_key="delivery-key-1",
        )

    monkeypatch.setenv(delivery.transport.LIVE_HANDOFF_ENV, "true")
    monkeypatch.setattr(
        delivery.transport,
        "create_signed_plan_transport",
        lambda **_kwargs: _signed(),
    )
    with pytest.raises(RuntimeError, match="base URL is not configured"):
        delivery.deliver_application_plan(
            plan_id="application-plan-delivery-1",
            idempotency_key="delivery-key-1",
        )


def test_delivery_posts_exact_bytes_and_records_only_correlated_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(delivery.transport.LIVE_HANDOFF_ENV, "true")
    monkeypatch.setenv(delivery.APPLY_BASE_URL_ENV, "http://apply:8000")
    signed = _signed()
    monkeypatch.setattr(
        delivery.transport,
        "create_signed_plan_transport",
        lambda **_kwargs: signed,
    )

    captured: dict[str, object] = {}

    def fake_urlopen(outbound, timeout):
        captured["url"] = outbound.full_url
        captured["method"] = outbound.get_method()
        captured["body"] = outbound.data
        captured["timeout"] = timeout
        return _Response(
            {
                "accepted": True,
                "replayed": False,
                "state": "PLAN_ACCEPTED",
                "handoff_id": "plan-handoff-delivery-1",
                "plan_id": "application-plan-delivery-1",
                "plan_digest": "a" * 64,
                "error": None,
            }
        )

    monkeypatch.setattr(delivery.request, "urlopen", fake_urlopen)

    recorded: list[dict[str, str]] = []

    def record_plan_accepted(**kwargs):
        recorded.append(dict(kwargs))
        return {
            "handoff_id": kwargs["handoff_id"],
            "plan_id": kwargs["plan_id"],
            "state": "PLAN_ACCEPTED",
            "submission_authority": False,
        }

    monkeypatch.setattr(delivery.transport, "record_plan_accepted", record_plan_accepted)

    result = delivery.deliver_application_plan(
        plan_id="application-plan-delivery-1",
        idempotency_key="delivery-key-1",
    )

    assert captured == {
        "url": "http://apply:8000/v1/application-plan-handoffs",
        "method": "POST",
        "body": signed.body,
        "timeout": 5.0,
    }
    assert recorded == [
        {
            "handoff_id": "plan-handoff-delivery-1",
            "plan_id": "application-plan-delivery-1",
            "plan_digest": "a" * 64,
        }
    ]
    assert result.state == "PLAN_ACCEPTED"
    assert result.replayed is False
    assert result.submission_authority is False


def test_delivery_refuses_uncorrelated_ack_without_recording_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(delivery.transport.LIVE_HANDOFF_ENV, "true")
    monkeypatch.setenv(delivery.APPLY_BASE_URL_ENV, "http://apply:8000")
    monkeypatch.setattr(
        delivery.transport,
        "create_signed_plan_transport",
        lambda **_kwargs: _signed(),
    )
    monkeypatch.setattr(
        delivery.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "accepted": True,
                "replayed": False,
                "state": "PLAN_ACCEPTED",
                "handoff_id": "wrong-handoff",
                "plan_id": "application-plan-delivery-1",
                "plan_digest": "a" * 64,
            }
        ),
    )

    called = False

    def forbidden_record(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("uncorrelated ACK must never be recorded")

    monkeypatch.setattr(delivery.transport, "record_plan_accepted", forbidden_record)

    with pytest.raises(RuntimeError, match="not correlated"):
        delivery.deliver_application_plan(
            plan_id="application-plan-delivery-1",
            idempotency_key="delivery-key-1",
        )

    assert called is False
