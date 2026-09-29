from __future__ import annotations

from pathlib import Path

from app import profile_answer_center_ui_v3 as answer_v3


ROOT = Path(__file__).resolve().parents[1]


def _full_details() -> dict[str, object]:
    return {
        "prior_employee": False,
        "phone_device_type": "Mobile",
        "phone_country_code": "+1",
        "location_work_eligible": False,
        "age_18_plus": True,
        "sponsorship_required": True,
        "gender": "candidate-entered-gender",
        "race_ethnicity": "candidate-entered-race",
        "ethnicity": "",
        "hispanic_latino": False,
        "veteran": False,
        "disability": False,
    }


def _readiness(*, salary_ready: bool) -> dict[str, object]:
    return {
        "rows": [
            {
                "question_key": "compensation.salary",
                "ready": salary_ready,
            }
        ]
    }


def test_workday_false_values_count_as_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        answer_v3.v2.profile_details,
        "load_candidate_profile_details",
        _full_details,
    )

    projection = answer_v3.workday_readiness_projection(
        _readiness(salary_ready=True)
    )

    assert projection["configured_count"] == projection["total_count"]
    assert projection["unanswered"] == []


def test_workday_salary_remains_unanswered_until_answer_vault_has_it(monkeypatch) -> None:
    monkeypatch.setattr(
        answer_v3.v2.profile_details,
        "load_candidate_profile_details",
        _full_details,
    )

    projection = answer_v3.workday_readiness_projection(
        _readiness(salary_ready=False)
    )

    assert [row["question_key"] for row in projection["unanswered"]] == [
        "compensation.salary"
    ]


def test_workday_race_uses_legacy_encrypted_fallback(monkeypatch) -> None:
    values = _full_details()
    values["race_ethnicity"] = ""
    values["ethnicity"] = "legacy-confirmed-race"
    monkeypatch.setattr(
        answer_v3.v2.profile_details,
        "load_candidate_profile_details",
        lambda: values,
    )

    projection = answer_v3.workday_readiness_projection(
        _readiness(salary_ready=True)
    )

    assert "self_id.race_ethnicity" not in {
        row["question_key"] for row in projection["unanswered"]
    }


def test_workday_race_save_updates_canonical_and_legacy_encrypted_fields(
    monkeypatch,
) -> None:
    values = _full_details()
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(
        answer_v3.v2.profile_details,
        "load_candidate_profile_details",
        lambda: dict(values),
    )
    monkeypatch.setattr(
        answer_v3.v2.profile_details,
        "save_candidate_profile_details",
        lambda payload: saved.append(dict(payload)),
    )

    spec = answer_v3._workday_spec("self_id.race_ethnicity")
    answer_v3._save_workday_answer(spec, "candidate-confirmed-choice")

    assert len(saved) == 1
    assert saved[0]["race_ethnicity"] == "candidate-confirmed-choice"
    assert saved[0]["ethnicity"] == "candidate-confirmed-choice"


def test_workday_salary_saves_only_through_normal_answer_vault(monkeypatch) -> None:
    saved_answers: list[tuple[str, str, bool]] = []
    profile_writes: list[object] = []

    monkeypatch.setattr(
        answer_v3.v2.answer_router,
        "save_user_answer",
        lambda question, answer, *, autofill_allowed: saved_answers.append(
            (question, answer, autofill_allowed)
        ),
    )
    monkeypatch.setattr(
        answer_v3.v2.profile_details,
        "save_candidate_profile_details",
        lambda payload: profile_writes.append(payload),
    )

    spec = answer_v3._workday_spec("compensation.salary")
    answer_v3._save_workday_answer(spec, "candidate-confirmed-salary-answer")

    assert saved_answers == [
        (
            "What are your salary expectations?",
            "candidate-confirmed-salary-answer",
            True,
        )
    ]
    assert profile_writes == []


def test_runtime_only_workday_fields_are_not_standing_answers() -> None:
    policies = dict(answer_v3._WORKDAY_RUNTIME_POLICIES)

    assert "Terms and conditions" in policies
    assert "never saved as a standing Yes" in policies["Terms and conditions"]
    assert "Self-identification date" in policies
    assert "execution date" in policies["Self-identification date"]
    assert "Phone extension" in policies
    assert "Blank is a valid confirmed state" in policies["Phone extension"]


def test_workday_profile_ui_has_no_employer_or_candidate_hardcoding() -> None:
    source = (ROOT / "app" / "profile_answer_center_ui_v3.py").read_text(
        encoding="utf-8"
    ).casefold()

    for forbidden in (
        "crescent energy",
        "crescentenergy",
        "r441",
        "4465",
        "8563284514",
        "11 west broad street",
        "palmyra",
        "08065",
        "mohammad aadil",
    ):
        assert forbidden not in source
