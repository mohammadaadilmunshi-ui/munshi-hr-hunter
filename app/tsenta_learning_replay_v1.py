"""Held-out, provider-neutral validation gate for MUNSHI Relay-derived Native hypotheses.

This module cannot perform submissions, change candidate truth, or promote a
new field mapping by itself. It evaluates evidence-backed candidate rules
after 10-15 distinct completed process observations and exposes priority-only
pilot readiness if there is a measured improvement without per-ATS regressions.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.tsenta_learning_cohort_v1 import COHORT_MIN

_SHA = re.compile(r"^[a-f0-9]{64}$")
MIN_HELD_OUT = 5
MIN_GAIN = 0.10


def evaluate_native_replay(
    *,
    cohort: Mapping[str, Any],
    replay_cases: Sequence[Mapping[str, Any]],
    training_application_digests: set[str],
) -> dict[str, Any]:
    """Compare old and candidate Native field recognition on independent cases.

    The caller supplies known training application hashes to prevent reusing
    the same provider records as both training and hold-out evidence.
    """
    result = {
        "version": "munshi-native-replay-evaluation-v1",
        "state": "INSUFFICIENT_EVIDENCE",
        "pilot_scope": None,
        "held_out_applications": 0,
        "platforms_tested": 0,
        "baseline_field_accuracy": None,
        "candidate_field_accuracy": None,
        "field_accuracy_gain": None,
        "ats_regressions": 0,
        "promotion_scope": "NON_SENSITIVE_FIELD_PRIORITY_ONLY",
        "live_answers_changed": False,
        "auto_submit_authorized": False,
        "raw_values_saved": False,
    }
    if (
        int(cohort.get("cohort_processed") or 0) < COHORT_MIN
        or cohort.get("phase") not in {"EVALUATION_READY", "COHORT_COMPLETE"}
    ):
        return result
    if not 0 < len(replay_cases) <= 50:
        return result
    if (
        len(training_application_digests) < COHORT_MIN
        or not all(_SHA.fullmatch(x) for x in training_application_digests)
    ):
        raise ValueError("Training identities must be SHA-256 digests.")

    unique: set[str] = set()
    baseline_correct = candidate_correct = observed_total = 0
    by_platform: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in replay_cases:
        identity = str(case.get("application_ref_sha256") or "")
        platform = str(case.get("ats_type") or "").strip().lower()
        if not _SHA.fullmatch(identity) or identity in unique:
            raise ValueError("A distinct replay application digest is required.")
        if identity in training_application_digests:
            raise ValueError("Replay cannot reuse a training application.")
        unique.add(identity)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", platform):
            raise ValueError("Replay ATS identity must be a safe dynamic slug.")
        observed = case.get("observed_fields")
        before = case.get("baseline_predictions")
        after = case.get("candidate_predictions")
        if not (
            isinstance(observed, list) and isinstance(before, list)
            and isinstance(after, list)
        ):
            raise TypeError("Replay must supply observed field and prediction lists.")
        if len(observed) > 200 or len(before) > 200 or len(after) > 200:
            raise ValueError("Replay contains too many field observations.")

        observed_by_id = {
            str(field.get("field_fingerprint") or ""): field
            for field in observed
            if field.get("evidence_grade") == "OBSERVED"
            and field.get("observation_kind") == "FORM_FIELD"
            and _SHA.fullmatch(str(field.get("field_fingerprint") or ""))
        }
        if not observed_by_id:
            raise ValueError("Replay has no independently observed field shape.")

        def _evaluate(
            predictions: list[dict[str, Any]],
            observed_by_id: dict[str, Any] = observed_by_id,
        ) -> tuple[int, int]:
            """Credit recognition only when observed field metadata is matched.

            A predicted fingerprint with *no properties* is not a successful
            field match. Missing required attributes must not count as
            correct by vacuous truth.
            """
            by_id = {
                str(row.get("field_fingerprint") or ""): row
                for row in predictions
                if isinstance(row, dict)
                and _SHA.fullmatch(str(row.get("field_fingerprint") or ""))
            }
            correct = 0
            keys = ("required", "editable", "has_value", "option_count")
            for fingerprint, observed_field in observed_by_id.items():
                candidate = by_id.get(fingerprint)
                if candidate is None:
                    continue
                comparable = [key for key in keys if key in observed_field]
                if (
                    comparable
                    and all(
                        key in candidate
                        and candidate[key] == observed_field[key]
                        for key in comparable
                    )
                ):
                    correct += 1
            extras = len(set(by_id) - set(observed_by_id))
            return correct, extras

        old, prior_extras = _evaluate(before)
        new, next_extras = _evaluate(after)
        if next_extras > prior_extras:
            result["ats_regressions"] += 1

        baseline_correct += old
        candidate_correct += new
        observed_total += len(observed_by_id)
        by_platform[platform][0] += old
        by_platform[platform][1] += new

    result["held_out_applications"] = len(unique)
    result["platforms_tested"] = len(by_platform)
    if len(unique) < MIN_HELD_OUT:
        return result
    result["baseline_field_accuracy"] = round(baseline_correct / observed_total, 4)
    result["candidate_field_accuracy"] = round(candidate_correct / observed_total, 4)
    result["field_accuracy_gain"] = round(
        (candidate_correct - baseline_correct) / observed_total, 4,
    )
    if any(new < old for old, new in by_platform.values()):
        result["ats_regressions"] += 1
    if (
        result["field_accuracy_gain"] >= MIN_GAIN
        and result["ats_regressions"] == 0
    ):
        result["state"] = "PRIORITY_ONLY_PILOT_READY"
        result["pilot_scope"] = (
            "MULTI_ATS" if len(by_platform) > 1 else "SINGLE_ATS"
        )
    else:
        result["state"] = "NO_SAFE_GAIN"
    return result
