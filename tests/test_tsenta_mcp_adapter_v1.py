from __future__ import annotations

import pytest

from app.tsenta_mcp_adapter_v1 import EXECUTION_ENV, TsentaMcpAdapter


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        responses = {
            "get_profile_status": {
                "onboardingComplete": True,
                "profileExtractionStatus": "completed",
                "hasResume": True,
                "missingForApplication": [],
            },
            "list_resume_profiles": [
                {"id": "profile-default", "name": "Default", "isDefault": True}
            ],
            "get_resume_profile": {
                "id": "profile-default",
                "isDefault": True,
                "data": {"personalInformation": {"firstName": "Private"}},
            },
            "get_apply_preferences": {
                "optimizationMode": "HONEST",
                "autoApproveResume": False,
                "autoSubmitApplication": True,
                "reviewBeforeSubmit": True,
            },
            "get_auto_apply_state": {
                "configExists": True,
                "config": {"isEnabled": False},
                "blockers": ["paused"],
            },
            "fetch_job_description": {
                "status": "success",
                "ats": "greenhouse",
                "jobDescription": "Fixture description",
            },
            "apply_to_job": {
                "applicationId": "provider-app-private",
                "status": "READY",
            },
            "request_resume_upload": {
                "uploadId": "upload-private",
                "uploadUrl": "https://uploads.example.com/signed-private",
                "maxBytes": 5_000_000,
            },
            "check_custom_resume": {"valid": True, "byteSize": 1234},
            "apply_with_custom_resume": {
                "applicationId": "synthetic-application-only",
                "status": "READY",
            },
        }
        return {"tool": tool_name, "result": responses[tool_name]}


def test_reads_all_authenticated_profile_sources_without_leaking_values():
    transport = FakeTransport()
    adapter = TsentaMcpAdapter(transport)

    snapshot = adapter.read_account_snapshot()
    summary = snapshot.privacy_safe_summary()

    assert [name for name, _ in transport.calls] == [
        "get_profile_status",
        "list_resume_profiles",
        "get_resume_profile",
        "get_apply_preferences",
        "get_auto_apply_state",
    ]
    assert transport.calls[2][1] == {"resumeProfileId": "profile-default"}
    assert summary["resume_profile_count"] == 1
    assert summary["raw_profile_values_in_output"] is False
    assert "Private" not in str(summary)


def test_capability_report_fails_closed_when_profile_tool_is_missing():
    adapter = TsentaMcpAdapter(FakeTransport(), available_tools=["get_profile_status"])
    report = adapter.capability_report()

    assert report["profile_read_ready"] is False
    assert report["execution_enabled"] is False
    with pytest.raises(RuntimeError, match="list_resume_profiles"):
        adapter.read_account_snapshot()


def test_job_description_and_apply_use_exact_verified_url(monkeypatch):
    transport = FakeTransport()
    adapter = TsentaMcpAdapter(transport)
    url = "https://job-boards.greenhouse.io/example/jobs/123"

    description = adapter.fetch_job_description(url)
    assert description["status"] == "success"
    with pytest.raises(PermissionError):
        adapter.apply_to_verified_job(url=url)
    monkeypatch.setenv(EXECUTION_ENV, "true")
    result = adapter.apply_to_verified_job(
        url=url,
        resume_profile_id="profile-default",
        job_description="Fixture description",
        execution_authorized=True,
    )

    assert result["employer_receipt_confirmed"] is False
    assert transport.calls[-1] == (
        "apply_to_job",
        {
            "url": url,
            "resumeProfileId": "profile-default",
            "jobDescription": "Fixture description",
        },
    )


def test_custom_resume_upload_is_checked_as_a_separate_step():
    transport = FakeTransport()
    adapter = TsentaMcpAdapter(transport)

    staged = adapter.request_resume_upload()
    checked = adapter.check_custom_resume(staged["uploadId"])

    assert checked["valid"] is True
    assert [name for name, _ in transport.calls] == [
        "request_resume_upload",
        "check_custom_resume",
    ]



def test_codex_json_event_nested_structured_result_is_unwrapped():
    from app.tsenta_mcp_adapter_v1 import _unwrap

    wrapped = {
        "tool": "get_profile_status",
        "status": "completed",
        "result": {
            "content": [{"type": "text", "text": "Redacted human output"}],
            "structured_content": {
                "tool": "get_profile_status",
                "result": {"onboardingComplete": True, "hasResume": True},
            },
        },
    }
    assert _unwrap(wrapped, tool_name="get_profile_status") == {
        "onboardingComplete": True,
        "hasResume": True,
    }


@pytest.mark.parametrize(
    "failed",
    [
        {"tool": "apply_to_job", "status": "failed", "error": "private"},
        {"tool": "apply_to_job", "isError": True, "content": []},
        {"tool": "apply_to_job", "result": {"is_error": True, "content": []}},
    ],
)
def test_tsenta_failure_envelopes_never_become_success(failed):
    from app.tsenta_mcp_adapter_v1 import _unwrap

    with pytest.raises(RuntimeError, match="reported a tool error") as error:
        _unwrap(failed, tool_name="apply_to_job")
    assert "private" not in str(error.value)


def test_text_only_json_mcp_result_is_parsed_when_no_structured_data():
    from app.tsenta_mcp_adapter_v1 import _unwrap

    wrapped = {"tool": "get_profile_status", "result": {
        "content": [{"type": "text", "text": '{\"hasResume\": true}'}]
    }}
    assert _unwrap(wrapped, tool_name="get_profile_status") == {"hasResume": True}



def test_custom_resume_apply_validates_upload_immediately_before_dispatch(monkeypatch):
    transport = FakeTransport()
    transport.calls.clear()
    adapter = TsentaMcpAdapter(transport)
    monkeypatch.setenv(EXECUTION_ENV, "true")
    # A fake provider response demonstrates that validation is mandatory,
    # without ever contacting Tsenta or an employer.
    result = adapter.apply_with_custom_resume(
            url="https://jobs.ashbyhq.com/example/123",
            upload_id="upload-private",
            complete_profile={"test": "synthetic"},
            execution_authorized=True,
        )
    assert result["employer_receipt_confirmed"] is False
    assert [tool for tool, _ in transport.calls] == [
        "check_custom_resume",
        "apply_with_custom_resume",
    ]


def test_custom_resume_stale_upload_cannot_reach_application_submission(monkeypatch):
    transport = FakeTransport()
    original_call = transport.__call__

    def stale_upload(tool_name, arguments):
        if tool_name == "check_custom_resume":
            transport.calls.append((tool_name, arguments))
            return {"result": {"valid": False}}
        return original_call(tool_name, arguments)

    adapter = TsentaMcpAdapter(stale_upload)
    monkeypatch.setenv(EXECUTION_ENV, "true")
    with pytest.raises(RuntimeError, match="not verified"):
        adapter.apply_with_custom_resume(
            url="https://jobs.ashbyhq.com/example/123",
            upload_id="upload-expired",
            complete_profile={"test": "synthetic"},
            execution_authorized=True,
        )
    assert [tool for tool, _ in transport.calls] == ["check_custom_resume"]
