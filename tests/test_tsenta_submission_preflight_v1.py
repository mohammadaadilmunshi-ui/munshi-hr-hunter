from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.tsenta_mcp_http_v1 import (
    TsentaMcpApplicationClient,
    TsentaMcpReadClient,
)
from app.tsenta_oauth_v1 import TsentaOAuthError
from app.tsenta_submission_preflight_v1 import (
    REQUIRED_TOOLS,
    submission_readiness,
    tsenta_profile_with_managed_email,
)

DOC = b"%PDF-1.4\n" + (b"synthetic content\n" * 15) + b"%%EOF"
ALIAS = "application-1@example.net"


def _profile():
    return {"data": {"personalInformation": {
        "firstName": "A", "lastName": "Test",
        "email": "original@example.net",
    }, "education": [{"institution": "Example"}]}}


def _route():
    return {
        "provider": "TSENTA", "state": "PREPARATION",
        "resume_sha256": hashlib.sha256(DOC).hexdigest(),
    }


def test_preflight_requires_bound_pdf_verified_profile_and_tools():
    s = submission_readiness(
        route=_route(), profile=_profile(), resume_pdf=DOC,
        managed_email=ALIAS, relay_ready=True, profile_verified=True,
        available_tools=REQUIRED_TOOLS,
    )
    assert s["ready_for_review"] is True
    assert s["submit_authorized"] is False
    assert ALIAS not in str(s)


def test_wrong_provider_or_stale_document_fails_closed():
    s = submission_readiness(
        route=_route() | {"provider": "NATIVE"},
        profile=_profile(), resume_pdf=DOC+b"tampered",
        managed_email=ALIAS, relay_ready=True, profile_verified=True,
        available_tools=REQUIRED_TOOLS,
    )
    assert s["ready_for_review"] is False
    assert "EXACT_OPENING_OWNED_BY_ANOTHER_ENGINE" in s["blockers"]
    assert "RESUME_BYTES_DIFFER_FROM_BOUND_PACKAGE" in s["blockers"]


def test_prior_dispatch_never_retreated_as_first_attempt():
    s = submission_readiness(
        route=_route() | {"state": "FILLING", "provider_application_id_sha256": "a"*64},
        profile=_profile(), resume_pdf=DOC,
        managed_email=ALIAS, relay_ready=True, profile_verified=True,
        available_tools=REQUIRED_TOOLS,
    )
    assert s["ready_for_review"] is False
    assert "PRIOR_PROVIDER_OR_RECEIPT_EVIDENCE_EXISTS" in s["blockers"]


def test_profile_keeps_other_tsenta_data_but_uses_existing_munshi_alias():
    original = _profile()
    clone = tsenta_profile_with_managed_email(
        profile=original, managed_email=ALIAS,
    )
    assert clone["personalInformation"]["email"] == ALIAS
    assert original["data"]["personalInformation"]["email"] == "original@example.net"
    assert clone["education"] == original["data"]["education"]


def test_no_unverified_or_invalid_alias_in_application_profile():
    with pytest.raises(ValueError):
        tsenta_profile_with_managed_email(profile=_profile(), managed_email="invalid")
    with pytest.raises(ValueError):
        tsenta_profile_with_managed_email(
            profile={"data": {"personalInformation": {}}},
            managed_email=ALIAS,
        )


def test_read_client_still_cannot_upload_or_apply():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(200, json={"jsonrpc":"2.0",
                "id":body["id"], "result":{"protocolVersion":"2025-11-25"}})
        if method == "tools/list":
            return httpx.Response(200, json={"jsonrpc":"2.0", "id":body["id"],
                "result":{"tools":[{"name":"upload-custom-resume"},
                                   {"name":"apply-with-custom-resume"},
                                   {"name":"get-profile-status"}]}})
        raise AssertionError("No write call should be issued")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        TsentaMcpReadClient("fake-token", client=client) as read,
    ):
            read.initialize()
            assert "apply_with_custom_resume" not in read._tools


def test_application_write_needs_explicit_one_use_claim_and_known_exact_tool():
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        body=json.loads(request.content)
        method=body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
              "result":{"protocolVersion":"2025-11-25"}})
        if method == "tools/list":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
              "result":{"tools":[{"name":"upload-custom-resume"},
                {"name":"check-custom-resume"},
                {"name":"apply-with-custom-resume"}]}})
        assert method == "tools/call"
        seen.append(body["params"]["name"])
        return httpx.Response(200,json={"jsonrpc":"2.0","id":body["id"],
          "result":{"structuredContent":{"result":{"uploadId":"example-upload"}}}})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        TsentaMcpApplicationClient("fake-token", client=client) as write,
    ):
            write.initialize()
            with pytest.raises(PermissionError):
                write.call_application(
                    "upload_custom_resume", {"fileBase64": "AAAA"}
                )
            with pytest.raises(ValueError):
                write.call_application("apply_with_custom_resume",
                    {"url":"https://example.org"},
                    claimed_for_single_use=True)
            with pytest.raises(TsentaOAuthError):
                write.call_application("apply_to_job",
                    {"url":"https://example.org"}, claimed_for_single_use=True)
            assert not seen
            result = write.call_application("upload_custom_resume",
                {"fileBase64": "AAAA"}, claimed_for_single_use=True)
            assert "structuredContent" in result
            assert seen == ["upload-custom-resume"]
