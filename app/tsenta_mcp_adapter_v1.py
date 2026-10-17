"""Authenticated Tsenta MCP boundary for MUNSHI.

The adapter knows only the documented MCP tool contracts.  Authentication and
transport are injected by the caller, so OAuth tokens, cookies and endpoint
credentials never enter Hunter source, SQLite, logs or handoff payloads.

Read operations are usable independently.  Application creation remains
double-gated by an explicit call-site authorization and an off-by-default
environment flag.  A successful provider call is never treated as an employer
receipt.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.linkedin_employer_link_resolver_v1 import external_url

ADAPTER_VERSION = "munshi-tsenta-mcp-adapter-v1"
EXECUTION_ENV = "MUNSHI_TSENTA_EXTERNAL_EXECUTION_ENABLED"

READ_TOOLS = frozenset(
    {
        "get_profile_status",
        "list_resume_profiles",
        "get_resume_profile",
        "get_apply_preferences",
        "get_auto_apply_state",
        "fetch_job_description",
        "get_application_status",
        "get_application_review",
        "list_applications",
        "list_tracked_applications",
        "request_resume_upload",
        "check_custom_resume",
    }
)
EXECUTION_TOOLS = frozenset({"apply_to_job", "apply_with_custom_resume"})
SUPPORTED_TOOLS = READ_TOOLS | EXECUTION_TOOLS


class TsentaTransport(Protocol):
    """Authenticated MCP invocation supplied by a trusted runtime."""

    def __call__(self, tool_name: str, arguments: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True)
class TsentaAccountSnapshot:
    """Ephemeral authorized account data; callers must not persist it wholesale."""

    profile_status: Mapping[str, Any]
    resume_profiles: tuple[Mapping[str, Any], ...]
    selected_resume_profile: Mapping[str, Any]
    apply_preferences: Mapping[str, Any]
    auto_apply_state: Mapping[str, Any]

    def privacy_safe_summary(self) -> dict[str, Any]:
        missing = self.profile_status.get("missingForApplication") or []
        selected_data = self.selected_resume_profile.get("data") or {}
        if not isinstance(selected_data, Mapping):
            selected_data = {}
        auto_config = self.auto_apply_state.get("config") or {}
        if not isinstance(auto_config, Mapping):
            auto_config = {}
        return {
            "version": ADAPTER_VERSION,
            "onboarding_complete": self.profile_status.get("onboardingComplete")
            is True,
            "profile_extraction_status": str(
                self.profile_status.get("profileExtractionStatus") or "unknown"
            ),
            "has_resume": self.profile_status.get("hasResume") is True,
            "missing_required_count": (
                len(missing)
                if isinstance(missing, Sequence)
                and not isinstance(missing, (str, bytes, bytearray))
                else 0
            ),
            "resume_profile_count": len(self.resume_profiles),
            "selected_profile_is_default": self.selected_resume_profile.get("isDefault")
            is True,
            "selected_profile_sections": sorted(str(key) for key in selected_data),
            "apply_preference_flags": {
                "optimization_mode": str(
                    self.apply_preferences.get("optimizationMode") or "unknown"
                ),
                "auto_approve_resume": self.apply_preferences.get("autoApproveResume")
                is True,
                "auto_submit_application": self.apply_preferences.get(
                    "autoSubmitApplication"
                )
                is True,
                "review_before_submit": self.apply_preferences.get("reviewBeforeSubmit")
                is True,
            },
            "auto_apply_configured": self.auto_apply_state.get("configExists") is True,
            "auto_apply_enabled": auto_config.get("isEnabled") is True,
            "auto_apply_blocker_count": len(
                self.auto_apply_state.get("blockers") or []
            ),
            "raw_profile_values_in_output": False,
        }


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _unwrap(response: Any, *, tool_name: str) -> Any:
    """Unwrap authenticated MCP tool results and fail closed on tool errors.

    Codex JSON events wrap an MCP tool result as
    {"tool": ..., "result": {"content": [...],
                             "structured_content": {"result": ...}}}.
    The MCP SDK instead exposes structuredContent/isError attributes.
    Never pass an error envelope to the application as successful data,
    and never surface raw provider error text containing private fields.
    """
    if getattr(response, "isError", False):
        raise RuntimeError(f"Tsenta MCP {tool_name} reported a tool error.")
    if hasattr(response, "structuredContent"):
        structured = response.structuredContent
        if isinstance(structured, Mapping):
            return _unwrap(structured, tool_name=tool_name)

    if not isinstance(response, Mapping):
        raise TypeError(
            f"Tsenta MCP {tool_name} returned an unsupported response shape."
        )
    if response.get("isError") is True or response.get("is_error") is True:
        raise RuntimeError(f"Tsenta MCP {tool_name} reported a tool error.")
    if str(response.get("status") or "").casefold() in {"failed", "error"} and (
        response.get("error") is not None or "tool" in response
    ):
        raise RuntimeError(f"Tsenta MCP {tool_name} reported a tool error.")

    structured = response.get("structuredContent") or response.get(
        "structured_content"
    )
    if isinstance(structured, Mapping):
        return _unwrap(structured, tool_name=tool_name)
    if "result" in response and (
        response.get("tool") or len(response) <= 3
    ):
        return _unwrap_result(response["result"], tool_name=tool_name)

    if "content" in response and isinstance(response["content"], list):
        content = response["content"]
        if len(content) == 1 and isinstance(content[0], Mapping):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except ValueError as error:
                    raise RuntimeError(
                        f"Tsenta MCP {tool_name} returned non-JSON tool content."
                    ) from error
        raise RuntimeError(
            f"Tsenta MCP {tool_name} did not provide structured tool data."
        )
    return dict(response)


def _unwrap_result(result: Any, *, tool_name: str) -> Any:
    """Preserve valid array/scalar results but unwrap nested SDK wrappers."""
    if isinstance(result, Mapping):
        return _unwrap(result, tool_name=tool_name)
    if isinstance(result, list):
        return result
    raise RuntimeError(
        f"Tsenta MCP {tool_name} returned an unsupported tool result."
    )


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"Tsenta MCP {label} did not return an object.")
    return dict(value)


class TsentaMcpAdapter:
    """Thin, schema-bound adapter over an already-authenticated MCP transport."""

    def __init__(
        self,
        transport: TsentaTransport | Callable[[str, Mapping[str, Any]], Any],
        *,
        available_tools: Sequence[str] | None = None,
    ) -> None:
        if not callable(transport):
            raise TypeError("Tsenta MCP transport must be callable.")
        self._transport = transport
        self._available_tools = (
            frozenset(str(item) for item in available_tools)
            if available_tools is not None
            else SUPPORTED_TOOLS
        )

    def capability_report(self) -> dict[str, Any]:
        available = sorted(SUPPORTED_TOOLS & self._available_tools)
        return {
            "version": ADAPTER_VERSION,
            "available": available,
            "missing": sorted(SUPPORTED_TOOLS - self._available_tools),
            "profile_read_ready": {
                "get_profile_status",
                "list_resume_profiles",
                "get_resume_profile",
                "get_apply_preferences",
                "get_auto_apply_state",
            }.issubset(self._available_tools),
            "default_resume_apply_schema_available": "apply_to_job"
            in self._available_tools,
            "custom_resume_apply_schema_available": "apply_with_custom_resume"
            in self._available_tools,
            "execution_enabled": _truthy(os.getenv(EXECUTION_ENV)),
            "authentication_material_in_report": False,
        }

    def _call(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        if tool_name not in SUPPORTED_TOOLS:
            raise ValueError("Unsupported Tsenta connector tool.")
        if tool_name not in self._available_tools:
            raise RuntimeError(f"Tsenta MCP capability unavailable: {tool_name}")
        response = self._transport(tool_name, dict(arguments or {}))
        return _unwrap(response, tool_name=tool_name)

    def read_account_snapshot(
        self, *, resume_profile_id: str | None = None
    ) -> TsentaAccountSnapshot:
        status = _mapping(self._call("get_profile_status"), label="get_profile_status")
        profiles_raw = self._call("list_resume_profiles")
        if not isinstance(profiles_raw, Sequence) or isinstance(
            profiles_raw, (str, bytes, bytearray)
        ):
            raise TypeError("Tsenta MCP list_resume_profiles did not return a list.")
        profiles = tuple(
            _mapping(item, label="resume profile") for item in profiles_raw
        )
        if not profiles:
            raise RuntimeError("Tsenta account has no resume profile.")
        selected = None
        if resume_profile_id:
            selected = next(
                (
                    item
                    for item in profiles
                    if str(item.get("id") or "") == str(resume_profile_id)
                ),
                None,
            )
            if selected is None:
                raise LookupError("Requested Tsenta resume profile was not found.")
        else:
            selected = next(
                (item for item in profiles if item.get("isDefault") is True),
                profiles[0],
            )
        selected_id = str(selected.get("id") or "").strip()
        if not selected_id:
            raise RuntimeError("Tsenta resume profile has no stable id.")
        full_profile = _mapping(
            self._call("get_resume_profile", {"resumeProfileId": selected_id}),
            label="get_resume_profile",
        )
        preferences = _mapping(
            self._call("get_apply_preferences"), label="get_apply_preferences"
        )
        auto_state = _mapping(
            self._call("get_auto_apply_state"), label="get_auto_apply_state"
        )
        return TsentaAccountSnapshot(
            profile_status=status,
            resume_profiles=profiles,
            selected_resume_profile=full_profile,
            apply_preferences=preferences,
            auto_apply_state=auto_state,
        )

    def compare_candidate_truth(
        self,
        *,
        account_snapshot: TsentaAccountSnapshot,
        candidate_snapshot: Mapping[str, Any],
        protected_candidate_values: Mapping[str, Any],
        explicit_profile_read_approval: bool = False,
    ) -> dict[str, Any]:
        from app.tsenta_profile_bridge_v1 import compare_candidate_truth_snapshot

        return compare_candidate_truth_snapshot(
            candidate_snapshot,
            protected_candidate_values,
            account_snapshot.selected_resume_profile,
            source="authorized_tsenta_mcp",
            explicit_profile_read_approval=explicit_profile_read_approval,
        )

    def fetch_job_description(self, url: str) -> dict[str, Any]:
        verified = external_url(url)
        if not verified:
            raise ValueError(
                "Tsenta job intake requires an external HTTPS employer URL."
            )
        return _mapping(
            self._call("fetch_job_description", {"url": verified}),
            label="fetch_job_description",
        )

    def list_applications(
        self,
        *,
        filter_name: str = "all",
        page: int = 1,
        limit: int = 25,
    ) -> dict[str, Any]:
        if filter_name not in {
            "all",
            "submitted",
            "in_flight",
            "needs",
            "failed",
            "skipped",
        }:
            raise ValueError("Unsupported Tsenta application filter.")
        return _mapping(
            self._call(
                "list_applications",
                {
                    "filter": filter_name,
                    "includeCounts": True,
                    "page": max(1, int(page)),
                    "limit": max(1, min(int(limit), 100)),
                },
            ),
            label="list_applications",
        )

    def application_status(self, application_id: str) -> dict[str, Any]:
        value = str(application_id or "").strip()
        if not value:
            raise ValueError("Tsenta application id is required.")
        return _mapping(
            self._call("get_application_status", {"applicationId": value}),
            label="get_application_status",
        )

    def application_review(self, application_id: str) -> dict[str, Any]:
        value = str(application_id or "").strip()
        if not value:
            raise ValueError("Tsenta application id is required.")
        return _mapping(
            self._call("get_application_review", {"applicationId": value}),
            label="get_application_review",
        )

    def list_tracked_applications(
        self,
        *,
        page: int = 1,
        limit: int = 25,
        query: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "page": max(1, int(page)),
            "limit": max(1, min(int(limit), 100)),
        }
        if query:
            arguments["query"] = str(query)[:200]
        return _mapping(
            self._call("list_tracked_applications", arguments),
            label="list_tracked_applications",
        )

    def request_resume_upload(self) -> dict[str, Any]:
        """Return ephemeral upload metadata; callers must not persist its signed URL."""
        result = _mapping(
            self._call("request_resume_upload"), label="request_resume_upload"
        )
        if not str(result.get("uploadId") or "").strip() or not str(
            result.get("uploadUrl") or ""
        ).startswith("https://"):
            raise RuntimeError("Tsenta did not return a valid staged resume upload.")
        return result

    def check_custom_resume(self, upload_id: str) -> dict[str, Any]:
        value = str(upload_id or "").strip()
        if not value:
            raise ValueError("Tsenta custom-resume upload id is required.")
        return _mapping(
            self._call("check_custom_resume", {"uploadId": value}),
            label="check_custom_resume",
        )

    def apply_to_verified_job(
        self,
        *,
        url: str,
        resume_profile_id: str | None = None,
        job_description: str | None = None,
        execution_authorized: bool = False,
    ) -> dict[str, Any]:
        """Create a Tsenta application only after both independent gates pass."""
        if not execution_authorized:
            raise PermissionError("Tsenta application execution was not authorized.")
        if not _truthy(os.getenv(EXECUTION_ENV)):
            raise RuntimeError("Tsenta external execution is disabled.")
        verified = external_url(url)
        if not verified:
            raise ValueError("A verified external employer URL is required.")
        arguments: dict[str, Any] = {"url": verified}
        if resume_profile_id:
            arguments["resumeProfileId"] = str(resume_profile_id)
        if job_description:
            arguments["jobDescription"] = str(job_description)
        result = _mapping(self._call("apply_to_job", arguments), label="apply_to_job")
        return {
            "version": ADAPTER_VERSION,
            "provider": "TSENTA",
            "provider_result": result,
            "employer_receipt_confirmed": False,
        }

    @staticmethod
    def _verified_custom_resume(
        checked: Mapping[str, Any],
        *,
        upload_id: str,
        expected_byte_size: int | None = None,
    ) -> bool:
        """Accept either the documented boolean or live metadata response.

        Live Tsenta check-custom-resume currently echoes uploadId, byteSize,
        viewUrl instead of a valid boolean. The identity and nonzero size
        must agree; never accept mismatched IDs, explicit invalid results,
        or failed/incomplete staging records.
        """
        if checked.get("valid") is False:
            return False
        returned_id = checked.get("uploadId")
        if returned_id is not None and str(returned_id) != upload_id:
            return False
        size = checked.get("byteSize")
        if expected_byte_size is not None and (
            type(size) is not int or size != expected_byte_size
        ):
            return False
        if checked.get("valid") is True:
            return True
        view = checked.get("viewUrl")
        return bool(
            isinstance(returned_id, str)
            and returned_id == upload_id
            and type(size) is int
            and size >= 100
            and isinstance(view, str)
            and view.startswith("https://")
        )

    def apply_with_custom_resume(
        self,
        *,
        url: str,
        upload_id: str,
        complete_profile: Mapping[str, Any],
        execution_authorized: bool = False,
        expected_byte_size: int | None = None,
    ) -> dict[str, Any]:
        if not execution_authorized:
            raise PermissionError("Tsenta custom-resume execution was not authorized.")
        if not _truthy(os.getenv(EXECUTION_ENV)):
            raise RuntimeError("Tsenta external execution is disabled.")
        verified = external_url(url)
        if not verified:
            raise ValueError("A verified external employer URL is required.")
        if not str(upload_id or "").strip():
            raise ValueError("Validated Tsenta custom-resume upload id is required.")
        if not isinstance(complete_profile, Mapping) or not complete_profile:
            raise ValueError("A complete custom-resume profile is required.")
        # Upload IDs are temporary.  A prior successful check can go stale while
        # a request is queued; revalidate immediately before any execution call.
        checked = self.check_custom_resume(str(upload_id))
        if not self._verified_custom_resume(
            checked, upload_id=str(upload_id),
            expected_byte_size=expected_byte_size,
        ):
            raise RuntimeError("Tsenta custom resume was not verified for dispatch.")
        result = _mapping(
            self._call(
                "apply_with_custom_resume",
                {
                    "url": verified,
                    "uploadId": str(upload_id),
                    "profile": dict(complete_profile),
                },
            ),
            label="apply_with_custom_resume",
        )
        return {
            "version": ADAPTER_VERSION,
            "provider": "TSENTA",
            "provider_result": result,
            "employer_receipt_confirmed": False,
        }
