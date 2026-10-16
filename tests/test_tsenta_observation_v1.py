from __future__ import annotations

import json
from pathlib import Path

from app import database
from app import tsenta_observation_v1 as observations


def test_review_observation_keeps_shape_but_drops_answers_and_wire_values():
    rows = observations.observations_from_review(
        provider_application_id="private-application-id",
        ats_type="brassring",
        observed_at="2026-10-15T12:00:00+00:00",
        review={
            "awaitingReview": True,
            "fields": [
                {
                    "fieldId": "private-field-id",
                    "label": "Will you now or in the future require sponsorship?",
                    "kind": "select",
                    "value": "private-answer",
                    "display": "Private display",
                    "required": True,
                    "editable": True,
                    "options": [
                        {"label": "Yes", "value": "private-wire-yes"},
                        {"label": "No", "value": "private-wire-no"},
                    ],
                }
            ],
        },
    )

    assert len(rows) == 1
    assert rows[0]["has_value"] is True
    assert rows[0]["option_count"] == 2
    assert rows[0]["answer_value_recorded"] is False
    rendered = json.dumps(rows)
    for private in (
        "private-application-id",
        "private-field-id",
        "private-answer",
        "Private display",
        "private-wire-yes",
    ):
        assert private not in rendered


def test_observations_persist_idempotently_as_an_allowlisted_projection(hunter_db):
    del hunter_db
    rows = observations.observations_from_review(
        provider_application_id="provider-app-1",
        ats_type="successfactors",
        observed_at="2026-10-15T12:00:00+00:00",
        review={
            "awaitingReview": True,
            "fields": [
                {
                    "label": "State/Province",
                    "kind": "text",
                    "value": None,
                    "required": True,
                    "editable": True,
                }
            ],
        },
    )

    assert observations.persist_observations(rows) == {"inserted": 1, "replayed": 0}
    assert observations.persist_observations(rows) == {"inserted": 0, "replayed": 1}
    connection = database.get_connection()
    try:
        stored = dict(
            connection.execute("SELECT * FROM tsenta_observations_v1").fetchone()
        )
    finally:
        connection.close()
    assert stored["has_value"] == 0
    assert "provider-app-1" not in json.dumps(stored)


def test_native_delta_distinguishes_observed_and_inferred_fields():
    observed = observations.observations_from_review(
        provider_application_id="provider-app-2",
        ats_type="dayforce",
        review={
            "awaitingReview": True,
            "fields": [
                {
                    "label": "I agree to the legal attestation",
                    "kind": "checkbox",
                    "value": False,
                    "required": True,
                    "editable": True,
                }
            ],
        },
    )
    predictions = [
        {
            "prediction_id": "prediction-extra",
            "field_fingerprint": "f" * 64,
            "semantic_question_key": "contact.region",
            "field_kind": "text",
        }
    ]

    comparison = observations.compare_native_predictions(observed, predictions)

    assert comparison["answer_values_compared"] is False
    assert {row["evidence_grade"] for row in comparison["deltas"]} == {
        "OBSERVED",
        "INFERRED",
    }
    assert comparison["browser_internals_available"] is False


def test_status_observation_never_claims_employer_receipt():
    row = observations.observation_from_status(
        provider_application_id="provider-app-3",
        ats_type="indeed",
        status={
            "status": "FAILED",
            "errorCode": "INDEED_SCREENER_REJECTED",
            "isTerminal": True,
            "isRefunded": True,
            "statusMessage": {"title": "Screening response rejected"},
        },
    )

    assert row["provider_status"] == "FAILED"
    assert row["error_code"] == "INDEED_SCREENER_REJECTED"
    assert row["employer_receipt_observed"] is False


def test_sanitized_mcp_review_fixture_covers_observed_ats_shapes():
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "tsenta_mcp_application_reviews_sanitized_v1.json"
    )
    fixture = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for index, case in enumerate(fixture["cases"]):
        rows.extend(
            observations.observations_from_review(
                provider_application_id=f"fixture-application-{index}",
                ats_type=case["ats_type"],
                review=case["review"],
            )
        )

    assert {row["ats_type"] for row in rows} == {
        "dayforce",
        "successfactors",
        "brassring",
    }
    assert any(row["field_kind"] == "file" for row in rows)
    assert any(row["required"] and not row["has_value"] for row in rows)
    assert all(row["answer_value_recorded"] is False for row in rows)



def test_unseen_ats_display_names_are_normalized_without_allowlist():
    assert observations._ats_slug("Oracle Cloud HCM") == "oracle-cloud-hcm"
    assert observations._ats_slug("UKG / Pro") == "ukg-pro"
    assert observations._ats_slug("Future ATS Vendor") == "future-ats-vendor"
    import pytest

    with pytest.raises(ValueError):
        observations._ats_slug("private@example.com")
    with pytest.raises(ValueError):
        observations._ats_slug("https://candidate.example.com")



def test_personalized_question_text_is_not_persisted(hunter_db):
    del hunter_db
    from app.database import get_connection

    rows = observations.observations_from_review(
        provider_application_id="private-id-not-stored",
        ats_type="future_ats",
        review={"awaitingReview": True, "fields": [
            {"label": "Dear Personal Name Here, your birth city?",
             "kind": "text", "required": True, "value": "PRIVATE_BIRTH_CITY"},
        ]},
    )
    observations.persist_observations(rows)
    with get_connection() as connection:
        row = connection.execute(
            "SELECT label_template, observed_json FROM tsenta_observations_v1"
        ).fetchone()
    assert row["label_template"] is None
    assert "Personal Name Here" not in row["observed_json"]
    assert "PRIVATE_BIRTH_CITY" not in row["observed_json"]



def test_provider_error_content_never_persists_as_error_code(hunter_db):
    del hunter_db
    from app.database import get_connection

    item = observations.observation_from_status(
        provider_application_id="private-app-status-999",
        ats_type="unknown-new-platform",
        status={
            "status": "FAILED", "isTerminal": True,
            "errorCode": "Applicant private@example.com failed validation",
            "statusMessage": {"title": "Private applicant personal details"},
        },
    )
    assert item["error_code"] is None
    observations.persist_observations([item])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT error_code,observed_json FROM tsenta_observations_v1"
        ).fetchone()
    assert row["error_code"] is None
    assert "private@example.com" not in row["observed_json"]
    assert "personal details" not in row["observed_json"]
