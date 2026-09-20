#!/usr/bin/env python3
"""Controlled staging AutoApply canary.

This command deliberately has no customer-approval or employer-submit action.
It has two modes:

* pre-approval: start/replay normal Prepare and require READY_FOR_APPROVAL.
* post-approval-observe: read only; after the user has approved once in the
  Initial Dashboard, wait until hosted ATS preparation reaches READY_FOR_REVIEW.

The staging compose/runtime must keep final submit disabled for both modes.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from app import product_application_flow_v2 as complete_loop


_FAILURE_STATES = frozenset({"FAILED_SAFELY", "BLOCKED", "CANCELLED"})
_SUBMISSION_STATES = frozenset(
    {"SUBMITTED", "SUBMISSION_UNVERIFIED", "VERIFIED", "AWAITING_RESPONSE"}
)


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def assert_staging_safety() -> None:
    if not _enabled("CLOUD_SHADOW_MODE"):
        raise RuntimeError("Canary requires CLOUD_SHADOW_MODE=true.")
    for name in (
        "PRODUCTION_CALLBACKS_ENABLED",
        "PRODUCTION_STATE_IMPORTED",
        "MUNSHI_FINAL_SUBMIT_ENABLED",
        "MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED",
    ):
        if _enabled(name):
            raise RuntimeError(f"Unsafe staging canary environment: {name}=true")


def _ats_task(flow: Mapping[str, Any]) -> dict[str, Any] | None:
    for raw in flow.get("tasks") or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("task_kind") or "") == "ATS_FORM_PREPARATION":
            return dict(raw)
    return None


def _summary(flow: Mapping[str, Any]) -> dict[str, Any]:
    ats = _ats_task(flow)
    output = dict((ats or {}).get("output") or {})
    return {
        "job_id": flow.get("job_id"),
        "state": flow.get("state"),
        "authority": flow.get("authority"),
        "prepare_status": (flow.get("prepare") or {}).get("status")
        if isinstance(flow.get("prepare"), Mapping)
        else None,
        "ats_task_status": (ats or {}).get("status"),
        "apply_prepare_state": output.get("apply_prepare_state"),
        "browser_preparation_completed": bool(
            output.get("browser_preparation_completed")
        ),
        "customer_approval_present": bool(flow.get("execution_mandate")),
        "production_receipt_present": bool(flow.get("production_receipt")),
        "submission_authority_projected": bool(flow.get("submission_authority")),
        "final_submit_executed": bool(output.get("final_submit_executed")),
    }


def _assert_no_submission(flow: Mapping[str, Any]) -> None:
    state = str(flow.get("state") or "")
    if state in _SUBMISSION_STATES:
        raise RuntimeError(
            "Preparation-only canary observed a submission state: " + state
        )
    if flow.get("production_receipt"):
        raise RuntimeError("Preparation-only canary observed a production receipt.")
    ats = _ats_task(flow)
    output = dict((ats or {}).get("output") or {})
    if bool(output.get("final_submit_executed")):
        raise RuntimeError("Preparation-only canary observed final submit execution.")


def _wait(
    read_fn: Callable[[int], dict[str, Any]],
    *,
    job_id: int,
    timeout_seconds: float,
    poll_seconds: float,
    predicate: Callable[[dict[str, Any]], bool],
    fail_on_needs_input: bool,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_seconds)
    latest: dict[str, Any] = {}
    while True:
        latest = dict(read_fn(int(job_id)))
        _assert_no_submission(latest)
        if predicate(latest):
            return latest

        state = str(latest.get("state") or "")
        if state in _FAILURE_STATES:
            raise RuntimeError(
                "Canary reached terminal failure: "
                + json.dumps(_summary(latest), sort_keys=True)
            )
        if fail_on_needs_input and state == "NEEDS_INPUT":
            raise RuntimeError(
                "Canary requires input before the one-review boundary: "
                + json.dumps(_summary(latest), sort_keys=True)
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Canary timed out: " + json.dumps(_summary(latest), sort_keys=True)
            )
        time.sleep(max(0.0, float(poll_seconds)))


def run_pre_approval(
    job_id: int,
    *,
    engine: str = "NATIVE",
    timeout_seconds: float = 600,
    poll_seconds: float = 2,
    start_fn: Callable[..., dict[str, Any]] | None = None,
    read_fn: Callable[[int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assert_staging_safety()
    starter = start_fn or complete_loop.start_prepare_for_job
    reader = read_fn or complete_loop.flow_for_job

    initial = dict(starter(int(job_id), engine=str(engine).upper()))
    if initial.get("execution_mandate"):
        raise RuntimeError(
            "Pre-approval canary found an existing customer execution mandate."
        )
    _assert_no_submission(initial)

    result = _wait(
        reader,
        job_id=int(job_id),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        predicate=lambda flow: str(flow.get("state") or "") == "READY_FOR_APPROVAL",
        fail_on_needs_input=True,
    )
    if result.get("execution_mandate"):
        raise RuntimeError("Canary crossed the customer approval boundary.")
    if not result.get("package_review"):
        raise RuntimeError("READY_FOR_APPROVAL is missing the exact package review.")
    if bool(result.get("submission_authority")):
        raise RuntimeError("Pre-approval flow unexpectedly projects submission authority.")
    return result


def run_post_approval_observer(
    job_id: int,
    *,
    timeout_seconds: float = 900,
    poll_seconds: float = 2,
    read_fn: Callable[[int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assert_staging_safety()
    reader = read_fn or complete_loop.flow_for_job

    def browser_prepared(flow: dict[str, Any]) -> bool:
        if not flow.get("execution_mandate"):
            return False
        ats = _ats_task(flow)
        if not ats or str(ats.get("status") or "") != "SUCCEEDED":
            return False
        output = dict(ats.get("output") or {})
        return (
            str(output.get("apply_prepare_state") or "") == "READY_FOR_REVIEW"
            and bool(output.get("browser_preparation_completed"))
            and not bool(output.get("final_submit_executed"))
        )

    return _wait(
        reader,
        job_id=int(job_id),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        predicate=browser_prepared,
        fail_on_needs_input=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument(
        "--phase",
        choices=("pre-approval", "post-approval-observe"),
        required=True,
    )
    parser.add_argument("--engine", choices=("NATIVE", "N8N"), default="NATIVE")
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()

    if args.job_id <= 0:
        raise SystemExit("--job-id must be a positive integer")

    if args.phase == "pre-approval":
        result = run_pre_approval(
            args.job_id,
            engine=args.engine,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        marker = "READY_FOR_APPROVAL"
    else:
        result = run_post_approval_observer(
            args.job_id,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        marker = "BROWSER_PREPARATION_READY_FOR_REVIEW"

    print("CANARY_PHASE=" + args.phase)
    print("CANARY_RESULT=" + marker)
    print("FINAL_SUBMIT_EXECUTED=NO")
    print("PRODUCTION_RECEIPT_CREATED=NO")
    print("SUMMARY=" + json.dumps(_summary(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
