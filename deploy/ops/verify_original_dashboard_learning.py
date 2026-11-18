#!/usr/bin/env python3
"""Read-only original-dashboard post-deployment smoke and learning checks.

Never calls submit, OAuth start, learning sync, approval, or any mutating route.
Only the Hunter FastAPI loopback target is accepted by default. HTTP transport
is explicitly local; do not copy API credentials to public URLs or logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a status API credential to a redirect destination."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


DEFAULT_ORIGIN = "http://127.0.0.1:8000"
MAX_BYTES = 250_000
TIMEOUT = 6


def _request(origin: str, path: str, headers: Mapping[str, str] | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(
        origin + path, headers={"Accept": "application/json", **dict(headers or {})},
        method="GET",
    )
    try:
        response = urllib.request.build_opener(_RejectRedirect()).open(
            request, timeout=TIMEOUT
        )
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Response exceeded safe bounds.")
        try:
            payload = json.loads(body) if body else None
        except (ValueError, UnicodeDecodeError):
            payload = None
        return int(response.status), payload


def run_readonly_smoke(
    *,
    origin: str = DEFAULT_ORIGIN,
    api_secret: str | None = None,
    status_secret: str | None = None,
) -> dict[str, Any]:
    if origin != DEFAULT_ORIGIN:
        raise ValueError("Only the Hunter FastAPI loopback is permitted.")
    checks: dict[str, str] = {}
    failures: list[str] = []

    def verify(name: str, path: str, expected: int, headers: dict[str, str] | None = None) -> Any:
        try:
            status, result = _request(origin, path, headers)
            if status != expected:
                failures.append(name)
                checks[name] = f"FAIL_HTTP_{status}"
                return None
            checks[name] = "PASS"
            return result
        except (urllib.error.URLError, OSError, ValueError):
            failures.append(name)
            checks[name] = "FAIL_TRANSPORT_OR_RESPONSE"
            return None

    health = verify("hunter_health", "/health", 200)
    if not isinstance(health, dict) or health.get("success") is not True:
        failures.append("hunter_health_payload")
        checks["hunter_health_payload"] = "FAIL"
    else:
        checks["hunter_health_payload"] = "PASS"

    # Check that learning and Tsenta metadata remain private by default.
    verify("cohort_requires_auth", "/api/integrations/tsenta/learning/cohort", 401)
    verify("tsenta_requires_auth", "/api/integrations/tsenta/status", 401)
    fake_id = "application-smoke-does-not-exist"
    bridge_path = "/api/application-provider-status/by-application/" + fake_id
    verify("bridge_requires_distinct_auth", bridge_path, 401)

    result: dict[str, Any] = {
        "target": "ORIGINAL_DASHBOARD_HUNTER_API_LOOPBACK",
        "mode": "READ_ONLY",
        "checks": checks,
        "failed": failures,
        "learning": {"verified_processed_applications": None,
                     "phase": "UNVERIFIED_NO_API_SECRET"},
        "tsenta": {"authenticated_backend": None,
                   "profile_read_verified": None,
                   "execution_ready": None},
        "live_submit_attempts": 0,
        "no_candidate_details_reported": True,
    }

    if api_secret:
        auth = {"X-Hunter-Secret": api_secret}
        status = verify("tsenta_status", "/api/integrations/tsenta/status", 200, auth)
        if isinstance(status, dict) and status.get("provider") == "TSENTA":
            result["tsenta"] = {
                "authenticated_backend": status.get("authenticated_backend") is True,
                "profile_read_verified": status.get("profile_read_verified") is True,
                "execution_ready": status.get("execution_ready") is True,
            }
        else:
            failures.append("tsenta_status_contract")
            checks["tsenta_status_contract"] = "FAIL"

        cohort = verify("learning_cohort", "/api/integrations/tsenta/learning/cohort", 200, auth)
        if (
            isinstance(cohort, dict)
            and cohort.get("version") == "munshi-provider-learning-cohort-v1"
            and type(cohort.get("processed_applications_total")) is int
            and cohort["processed_applications_total"] >= 0
            and type(cohort.get("cohort_processed")) is int
            and 0 <= cohort["cohort_processed"] <= 15
            and cohort.get("auto_upgrade_enabled") is False
        ):
            result["learning"] = {
                "verified_processed_applications": cohort["processed_applications_total"],
                "phase": str(cohort.get("phase") or "UNAVAILABLE")[:60],
                "cohort_progress": cohort["cohort_processed"],
                "ats_platforms": int(cohort.get("unique_ats_in_cohort") or 0),
                "auto_upgrade_enabled": False,
            }
            checks["learning_cohort_contract"] = "PASS"
        else:
            failures.append("learning_cohort_contract")
            checks["learning_cohort_contract"] = "FAIL"

    if status_secret:
        bridge = verify(
            "bridge_exact_unknown_application",
            bridge_path, 404,
            {"X-Munshi-Status-Bridge-Secret": status_secret},
        )
        # Do not surface an application payload even if backend disagrees.
        del bridge

    result["ready_for_learning_observation"] = (
        not failures
        and result["tsenta"]["authenticated_backend"] is True
        and result["tsenta"]["profile_read_verified"] is True
    )
    result["all_smoke_checks_passed"] = not failures
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify existing MUNSHI Hunter without writes")
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--strict-connected-tsenta", action="store_true")
    args = parser.parse_args()
    try:
        result = run_readonly_smoke(
            origin=args.origin,
            api_secret=os.getenv("HUNTER_API_SECRET"),
            status_secret=os.getenv("MUNSHI_HUNTER_STATUS_BRIDGE_SECRET"),
        )
    except ValueError:
        print(json.dumps({"all_smoke_checks_passed": False,
                          "reason": "Unsupported or unsafe smoke target."}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    passed = result["all_smoke_checks_passed"]
    if args.strict_connected_tsenta:
        passed = passed and result["ready_for_learning_observation"]
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
