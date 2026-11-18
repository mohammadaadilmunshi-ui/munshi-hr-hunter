from __future__ import annotations

import pytest

from app.tsenta_learning_replay_v1 import evaluate_native_replay


def _cohort(n=12):
    return {"phase": "EVALUATION_READY", "cohort_processed": n}


def _training():
    return {f"{i:064x}" for i in range(1, 11)}


def _replays(*, regression: bool = False):
    fields = [
        {
            "evidence_grade": "OBSERVED", "observation_kind": "FORM_FIELD",
            "field_fingerprint": letter * 64,
            "required": True, "has_value": True,
        }
        for letter in ("a", "b")
    ]
    cases = []
    for i in range(5):
        before = [{"field_fingerprint": "a" * 64, "required": True, "has_value": True}]
        after = before + [{"field_fingerprint": "b" * 64,
                           "required": True, "has_value": True}]
        if regression and i == 2:
            after = [{"field_fingerprint": "c" * 64, "required": True}]
        cases.append({
            "application_ref_sha256": f"{i + 100:064x}",
            "ats_type": ("oracle", "future_ats")[i % 2],
            "observed_fields": fields,
            "baseline_predictions": before,
            "candidate_predictions": after,
        })
    return cases


def test_after_twelve_training_apps_safe_holdout_improvement_is_pilot_only():
    result = evaluate_native_replay(
        cohort=_cohort(), replay_cases=_replays(),
        training_application_digests=_training(),
    )
    assert result["held_out_applications"] == 5
    assert result["platforms_tested"] == 2
    assert result["state"] == "PRIORITY_ONLY_PILOT_READY"
    assert result["pilot_scope"] == "MULTI_ATS"
    assert result["field_accuracy_gain"] == 0.5
    assert result["auto_submit_authorized"] is False
    assert result["live_answers_changed"] is False


def test_below_ten_applications_cannot_upgrade():
    result = evaluate_native_replay(
        cohort=_cohort(9), replay_cases=_replays(),
        training_application_digests=_training(),
    )
    assert result["state"] == "INSUFFICIENT_EVIDENCE"


def test_any_ats_regression_prevents_upgrade():
    result = evaluate_native_replay(
        cohort=_cohort(), replay_cases=_replays(regression=True),
        training_application_digests=_training(),
    )
    assert result["state"] == "NO_SAFE_GAIN"
    assert result["ats_regressions"] > 0


def test_train_holdout_overlap_or_duplicate_replay_is_rejected():
    copies = _replays()
    copies[0]["application_ref_sha256"] = next(iter(_training()))
    with pytest.raises(ValueError, match="training"):
        evaluate_native_replay(
            cohort=_cohort(), replay_cases=copies,
            training_application_digests=_training(),
        )
    repeated = _replays()
    repeated[1]["application_ref_sha256"] = repeated[0]["application_ref_sha256"]
    with pytest.raises(ValueError, match="distinct"):
        evaluate_native_replay(
            cohort=_cohort(), replay_cases=repeated,
            training_application_digests=_training(),
        )


def test_fewer_than_five_heldout_cases_cannot_upgrade():
    result = evaluate_native_replay(
        cohort=_cohort(), replay_cases=_replays()[:4],
        training_application_digests=_training(),
    )
    assert result["state"] == "INSUFFICIENT_EVIDENCE"


def test_unobserved_native_extra_control_is_not_a_safe_gain():
    cases = _replays()
    for case in cases:
        case["candidate_predictions"].append({
            "field_fingerprint": "c" * 64, "required": True,
        })
    result = evaluate_native_replay(
        cohort=_cohort(), replay_cases=cases,
        training_application_digests=_training(),
    )
    assert result["state"] == "NO_SAFE_GAIN"
    assert result["ats_regressions"] > 0



def test_fingerprint_without_metadata_is_not_correct_field_recognition():
    cases = _replays()
    for case in cases:
        # Previously a fingerprint alone was scored as fully correct because
        # absent metadata was treated as a match on all properties.
        case["candidate_predictions"] = [
            {"field_fingerprint": "a" * 64},
            {"field_fingerprint": "b" * 64},
        ]
    result = evaluate_native_replay(
        cohort=_cohort(),
        replay_cases=cases,
        training_application_digests=_training(),
    )
    assert result["state"] == "NO_SAFE_GAIN"
    assert result["candidate_field_accuracy"] == 0.0


def test_incorrect_required_status_blocks_false_improvement():
    cases = _replays()
    for case in cases:
        case["candidate_predictions"][1]["required"] = False
    result = evaluate_native_replay(
        cohort=_cohort(),
        replay_cases=cases,
        training_application_digests=_training(),
    )
    assert result["state"] == "NO_SAFE_GAIN"
    assert result["field_accuracy_gain"] == 0.0
