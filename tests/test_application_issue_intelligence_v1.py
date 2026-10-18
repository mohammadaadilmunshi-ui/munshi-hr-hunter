"""System-wide failure reporting and receipt-safe recovery, no job-specific ID."""
from datetime import datetime, timezone
import pytest
from app.application_issue_intelligence_v1 import safe_failure, submission_claim_exists
from app import application_view_model_v1 as view_model
from app import application_recovery_v1 as recovery


@pytest.mark.parametrize("code,expected", [
    ("APPLICATION_PREFLIGHT_PREPARE_FAILED","package"),
    ("APPLICATION_PREFLIGHT_VALIDATION_FAILED","validation"),
    ("APPLICATION_PREFLIGHT_REUSE_CHECK_FAILED","reuse"),
    ("APPLICATION_PREFLIGHT_OPPORTUNITY_FAILED","opportunity"),
    ("APPLICATION_PREFLIGHT_PACKAGE_FAILED","package"),
    ("APPLICATION_PREFLIGHT_COVER_LETTER_FAILED","cover-letter"),
    ("APPLICATION_PREFLIGHT_FINALIZE_FAILED","final"),
    ("APPLY_PREPARE_FAILED_SAFELY","browser"),
    ("ATS_FORM_PREPARATION_FAILED","employer application form"),
    ("NATIVE_RESUME_PREPARE_FAILED","Resume generation"),
    ("ANSWER_RESOLUTION_PREPARE_FAILED","Application-answer"),
    ("LEASE_EXPIRED","worker"),
])
def test_all_pipeline_failures_have_safe_meaning(code,expected):
    resolved,message=safe_failure([
        {"status":"FAILED","error_code":code,
         "error_message":"password=secret candidate@example.com"}])
    assert resolved==code
    assert expected.casefold() in message.casefold()
    assert "secret" not in message
    assert "candidate@example.com" not in message


def test_untrusted_provider_error_text_never_leaks():
    code,message=safe_failure([{"status":"FAILED","error_code":"<img src=x>",
         "error_message":"bearer secret value"}])
    assert code is None
    assert "secret" not in message


@pytest.mark.parametrize("route",[
    {"state":"PROVIDER_SUBMITTED"},
    {"dispatch_claim_exists":True},
    {"provider_submitted_at":"2026-10-18T21:00:00Z"},
    {"state":"EMPLOYER_CONFIRMED","employer_confirmed_at":"2026-10-18"},
])
def test_provider_evidence_blocks_all_retry_actions(route,monkeypatch):
    flow={
        "state":"FAILED_SAFELY","authority":"prepare",
        "provider_route":route, "production_receipt":None,
        "internal_integrity_review":None,
        "tasks":[{"task_kind":"APPLICATION_PREFLIGHT","label":"Application checks",
                  "status":"FAILED","error_code":"APPLICATION_PREFLIGHT_PREPARE_FAILED"}],
        "prepare":{"application_id":"application-sample",
                   "operation_id":"prepare-sample","updated_at":datetime.now(timezone.utc).isoformat()},
        "lifecycle":{},
    }
    monkeypatch.setattr(view_model.application_flow,"flow_for_job",lambda *_:flow)
    model=view_model.view_for_job(101,seed={
        "id":101,"title":"Test position","company_name":"Test Employer",
        "location_raw":"Remote",
        "remote_type":"Remote","hunter_score":88,
    },heartbeat={"state":"healthy"})
    assert model.submission_attempted is True
    assert model.allowed_recovery_actions==("refresh_state","clear_local_selection")
    assert "do not resubmit" in (model.issue_summary or "").casefold()
    monkeypatch.setattr(recovery.application_flow,"flow_for_job",lambda *_:flow)
    with pytest.raises(ValueError,match="Recovery is blocked"):
        recovery._pre_submit(101)


def test_unsubmitted_failure_shows_code_and_safe_retry(monkeypatch):
    flow={
        "state":"FAILED_SAFELY","authority":"prepare",
        "tasks":[{"task_kind":"ATS_FORM_PREPARATION","label":"Application checks",
                  "status":"FAILED","error_code":"ATS_FORM_PREPARATION_FAILED"}],
        "prepare":{"application_id":"app-test",
                   "operation_id":"prep-test","updated_at":datetime.now(timezone.utc).isoformat()},
        "lifecycle":None,
    }
    monkeypatch.setattr(view_model.application_flow,"flow_for_job",lambda *_:flow)
    model=view_model.view_for_job(102,seed={
        "id":102,"company_name":"Test Employer","title":"Test Role",
        "location_raw":"Remote","remote_type":"Remote",
    },heartbeat={"state":"healthy"})
    assert model.failure_code=="ATS_FORM_PREPARATION_FAILED"
    assert "retry_preparation" in model.allowed_recovery_actions
    assert model.submission_attempted is False
