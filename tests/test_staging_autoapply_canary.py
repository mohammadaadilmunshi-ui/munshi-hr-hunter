from __future__ import annotations

from pathlib import Path

import pytest

from scripts import staging_autoapply_canary as canary


def _safe_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "true")
    monkeypatch.setenv("PRODUCTION_CALLBACKS_ENABLED", "false")
    monkeypatch.setenv("PRODUCTION_STATE_IMPORTED", "false")
    monkeypatch.setenv("MUNSHI_FINAL_SUBMIT_ENABLED", "false")
    monkeypatch.setenv("MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED", "false")


def test_pre_approval_canary_stops_at_single_customer_review_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _safe_environment(monkeypatch)
    started: list[tuple[int, str]] = []

    def start(job_id: int, *, engine: str):
        started.append((job_id, engine))
        return {
            "job_id": job_id,
            "state": "RUNNING",
            "execution_mandate": None,
            "production_receipt": None,
            "submission_authority": False,
            "tasks": [],
        }

    def read(job_id: int):
        return {
            "job_id": job_id,
            "state": "READY_FOR_APPROVAL",
            "authority": "single_customer_approval_required",
            "package_review": {"review_digest": "a" * 64},
            "execution_mandate": None,
            "production_receipt": None,
            "submission_authority": False,
            "tasks": [
                {
                    "task_kind": "ATS_FORM_PREPARATION",
                    "status": "PENDING",
                    "output": {},
                }
            ],
        }

    result = canary.run_pre_approval(
        42,
        engine="NATIVE",
        timeout_seconds=1,
        poll_seconds=0,
        start_fn=start,
        read_fn=read,
    )

    assert started == [(42, "NATIVE")]
    assert result["state"] == "READY_FOR_APPROVAL"
    assert result["execution_mandate"] is None
    assert result["submission_authority"] is False


def test_post_approval_observer_is_read_only_and_requires_browser_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _safe_environment(monkeypatch)

    def read(job_id: int):
        return {
            "job_id": job_id,
            "state": "SUBMITTING",
            "authority": "single_customer_approval_recorded",
            "execution_mandate": {"mandate_id": "mandate-1"},
            "production_receipt": None,
            "submission_authority": False,
            "tasks": [
                {
                    "task_kind": "ATS_FORM_PREPARATION",
                    "status": "SUCCEEDED",
                    "output": {
                        "apply_prepare_state": "READY_FOR_REVIEW",
                        "browser_preparation_completed": True,
                        "final_submit_executed": False,
                    },
                }
            ],
        }

    result = canary.run_post_approval_observer(
        42,
        timeout_seconds=1,
        poll_seconds=0,
        read_fn=read,
    )

    summary = canary._summary(result)
    assert summary["customer_approval_present"] is True
    assert summary["browser_preparation_completed"] is True
    assert summary["apply_prepare_state"] == "READY_FOR_REVIEW"
    assert summary["final_submit_executed"] is False
    assert summary["production_receipt_present"] is False


def test_canary_refuses_any_submission_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _safe_environment(monkeypatch)

    def read(job_id: int):
        return {
            "job_id": job_id,
            "state": "VERIFIED",
            "execution_mandate": {"mandate_id": "mandate-1"},
            "production_receipt": {"receipt_id": "receipt-1"},
            "tasks": [],
        }

    with pytest.raises(RuntimeError, match="submission state|production receipt"):
        canary.run_post_approval_observer(
            42,
            timeout_seconds=1,
            poll_seconds=0,
            read_fn=read,
        )


def test_canary_requires_shadow_and_submit_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "false")
    monkeypatch.setenv("MUNSHI_FINAL_SUBMIT_ENABLED", "false")
    monkeypatch.setenv("MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED", "false")
    with pytest.raises(RuntimeError, match="CLOUD_SHADOW_MODE"):
        canary.assert_staging_safety()

    monkeypatch.setenv("CLOUD_SHADOW_MODE", "true")
    monkeypatch.setenv("MUNSHI_FINAL_SUBMIT_ENABLED", "true")
    with pytest.raises(RuntimeError, match="MUNSHI_FINAL_SUBMIT_ENABLED"):
        canary.assert_staging_safety()


def test_canary_source_has_no_customer_approval_or_submit_action() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "staging_autoapply_canary.py").read_text(
        encoding="utf-8"
    )
    wrapper = (
        root / "deploy" / "netcup" / "run_staging_autoapply_canary.sh"
    ).read_text(encoding="utf-8")

    assert "approve_and_submit_for_job(" not in source
    assert "approve_package(" not in source
    assert ".submit(" not in source
    assert "CUSTOMER_APPROVAL_CREATED_BY_CANARY=NO" in wrapper
    assert "EMPLOYER_SUBMIT_ACTION_AVAILABLE_TO_CANARY=NO" in wrapper
