"""Native Resume structured-output reliability + professional one-page fit hotfix.

This patch is intentionally presentation/generation-only. It grants no application,
browser, ATS, Gmail, n8n, outreach, or submission authority.

Why it exists:
* Resume Studio V2 lowered the Responses API output cap from V1's proven 10k to a
  6k default while also enabling reasoning. A complete evidence-cited resume JSON
  can therefore be cut off even though the visible resume itself is only one page.
* The V5 one-page guard sent the same "compact" repair instruction for both
  overflow and under-fill. A PDF reported as visibly under-filled could therefore
  be made even shorter.

The install is idempotent and scoped to the Native Resume UI process.
"""
from __future__ import annotations

import functools
import json
import re
from typing import Any

import httpx

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service as v1
from app import native_resume_service_v2 as v2
from app import resume_one_page_guard_v1 as one_page

VERSION = "native-resume-quality-hotfix-v1"
MARKER = "AADIL_NATIVE_RESUME_JSON_ONE_PAGE_HOTFIX_V1"

# V1 used 10k successfully. Keep user settings above this value, but do not let a
# lower saved setting starve the structured JSON transport. This is a transport
# envelope, not a request to make the visible resume longer.
MIN_STRUCTURED_OUTPUT_TOKENS = 10_000
MAX_STRUCTURED_OUTPUT_TOKENS = 12_000

# Professional density: clearly one page, but still breathable. The old 0.62 / .30
# thresholds accepted a visibly sparse page.
MIN_PROFESSIONAL_OCCUPIED_HEIGHT_RATIO = 0.70
MAX_PROFESSIONAL_BOTTOM_WHITESPACE_RATIO = 0.24
MAX_PROFESSIONAL_WORDS = 650
MAX_PROFESSIONAL_BULLET_WORDS = 28
MAX_PROFESSIONAL_ESTIMATED_LINES = 70

_PROFESSIONAL_ONE_PAGE_DIRECTION = (
    "Professional one-page requirement: create a polished US Letter resume that "
    "uses the page efficiently without looking crowded. Aim for roughly 520-640 "
    "visible words when the supplied evidence supports that much content, a 42-58 "
    "word summary, concise 18-28 word bullets, and normally 2-4 high-value bullets "
    "per relevant role. Preserve readable margins, section separation, and normal "
    "professional whitespace. Never pad with filler, duplicate claims, invented "
    "facts, unsupported skills, or unsupported metrics. Prefer the strongest "
    "JD-matched supported evidence, then supported projects/certifications when "
    "useful. The final PDF must be exactly one page and should visually occupy "
    "about 70-90% of the page height while staying easy to scan."
)


def _response_refusal(payload: dict[str, Any]) -> str:
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "refusal":
                continue
            value = content.get("refusal") or content.get("text")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _response_completion_guard(payload: dict[str, Any]) -> None:
    refusal = _response_refusal(payload)
    if refusal:
        raise RuntimeError("OpenAI declined the Native Resume generation request.")

    status = str(payload.get("status") or "").strip().casefold()
    if status == "incomplete":
        details = payload.get("incomplete_details") or {}
        reason = str(details.get("reason") or "unknown").strip().casefold()
        if reason == "max_output_tokens":
            raise RuntimeError(
                "Native Resume structured output was truncated before the JSON document completed. "
                "The transport budget is already at the safe resume envelope; retry the generation."
            )
        raise RuntimeError(f"Native Resume structured output was incomplete ({reason}).")
    if status == "failed":
        raise RuntimeError("OpenAI could not complete the Native Resume generation request.")


def _json_object(text: str) -> dict[str, Any]:
    """Parse one complete JSON object while tolerating harmless wrappers/trailing prose.

    Structured output should be a bare object. This fallback deliberately does not
    invent or repair missing JSON tokens: it only extracts an already-complete JSON
    object and lets Pydantic/evidence guards validate the result.
    """
    value = str(text or "").strip()
    if not value:
        raise RuntimeError("OpenAI returned no resume document text.")
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```\s*$", "", value).strip()

    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        if start < 0:
            raise RuntimeError("The model response was not valid resume JSON.")
        try:
            payload, _end = json.JSONDecoder().raw_decode(value[start:])
        except json.JSONDecodeError as error:
            raise RuntimeError("The model response was not valid resume JSON.") from error

    if not isinstance(payload, dict):
        raise RuntimeError("The model response must be a JSON object.")
    # Validate the full schema here so malformed-but-JSON output fails at the same
    # transport boundary, before any immutable resume/version persistence.
    try:
        v1.ResumeDocument.model_validate(payload)
    except Exception as error:
        raise RuntimeError("The model response did not match the Native Resume schema.") from error
    return payload


def _reliable_call_openai_v2(
    *,
    prompt_payload: dict[str, Any],
    config: dict[str, Any],
    rewrite_mode: str,
) -> tuple[dict[str, Any], str, str]:
    requested = int(config.get("max_output_tokens") or v2.DEFAULT_MAX_OUTPUT_TOKENS)
    transport_tokens = min(
        MAX_STRUCTURED_OUTPUT_TOKENS,
        max(MIN_STRUCTURED_OUTPUT_TOKENS, requested),
    )
    body = {
        "model": config["model"],
        "reasoning": {"effort": config["reasoning_effort"]},
        "max_output_tokens": transport_tokens,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": v2._system_prompt(rewrite_mode)}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(prompt_payload, ensure_ascii=False),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "munshi_native_resume_v2",
                "strict": False,
                "schema": v1.ResumeDocument.model_json_schema(),
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                "https://api.openai.com/v1/responses", headers=headers, json=body
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(
            f"OpenAI resume generation failed with HTTP {error.response.status_code}."
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("OpenAI resume generation could not be completed.") from error

    _response_completion_guard(data)
    payload = _json_object(v1._response_text(data))
    return payload, str(data.get("id") or "")[:200], str(config["model"])


def _adaptive_repair_instruction(
    *, issues: list[str], renderer_error: str | None = None
) -> str:
    renderer = str(renderer_error or "")
    underfilled = "under-filled" in renderer.casefold() or "underfilled" in renderer.casefold()
    if underfilled:
        return (
            "Create a fuller revision of this exact resume because the actual one-page PDF "
            "is visibly under-filled. Add only the strongest unused SUPPORTED evidence from "
            "the supplied candidate evidence and Stage B priorities. Strengthen relevant "
            "experience bullets, supported projects/certifications, or the summary as useful. "
            "Do not invent, duplicate, pad, or repeat claims merely to fill space. Keep every "
            "fact, metric, date and evidence reference truthful. "
            + _PROFESSIONAL_ONE_PAGE_DIRECTION
        )

    details = ", ".join(issues) if issues else "actual renderer overflow/page-fit issue"
    renderer_note = f" Renderer reported: {renderer[:400]}" if renderer else ""
    return (
        "Create a tighter revision of this exact resume. The current version failed the "
        f"one-page fit guard ({details}).{renderer_note} Compress or remove the lowest-value "
        "SUPPORTED material first, while preserving the strongest JD-matched evidence. "
        "Do not add new claims, alter metrics/dates, or shrink readability. "
        + _PROFESSIONAL_ONE_PAGE_DIRECTION
    )


def install() -> dict[str, Any]:
    """Install the idempotent Native Resume reliability/one-page quality patch."""
    current = getattr(v2, "_call_openai_v2", None)
    if not getattr(current, "_aadil_native_resume_reliable_json", False):
        _reliable_call_openai_v2._aadil_native_resume_reliable_json = True  # type: ignore[attr-defined]
        _reliable_call_openai_v2._aadil_prior_call = current  # type: ignore[attr-defined]
        v2._call_openai_v2 = _reliable_call_openai_v2

    one_page._ONE_PAGE_DIRECTION = _PROFESSIONAL_ONE_PAGE_DIRECTION
    one_page.MAX_SAFE_WORDS = MAX_PROFESSIONAL_WORDS
    one_page.MAX_SAFE_BULLET_WORDS = MAX_PROFESSIONAL_BULLET_WORDS
    one_page.MAX_SAFE_ESTIMATED_LINES = MAX_PROFESSIONAL_ESTIMATED_LINES
    one_page._repair_instruction = _adaptive_repair_instruction

    artifacts.MIN_OCCUPIED_HEIGHT_RATIO = MIN_PROFESSIONAL_OCCUPIED_HEIGHT_RATIO
    artifacts.MAX_BOTTOM_WHITESPACE_RATIO = MAX_PROFESSIONAL_BOTTOM_WHITESPACE_RATIO

    return {
        "success": True,
        "version": VERSION,
        "marker": MARKER,
        "structured_output_token_floor": MIN_STRUCTURED_OUTPUT_TOKENS,
        "max_structured_output_tokens": MAX_STRUCTURED_OUTPUT_TOKENS,
        "min_occupied_height_ratio": artifacts.MIN_OCCUPIED_HEIGHT_RATIO,
        "max_bottom_whitespace_ratio": artifacts.MAX_BOTTOM_WHITESPACE_RATIO,
        "max_safe_words": one_page.MAX_SAFE_WORDS,
        "max_safe_bullet_words": one_page.MAX_SAFE_BULLET_WORDS,
        "max_safe_estimated_lines": one_page.MAX_SAFE_ESTIMATED_LINES,
        "underfill_repair": "EXPAND_SUPPORTED_EVIDENCE",
        "overflow_repair": "COMPRESS_LOWEST_VALUE_SUPPORTED_EVIDENCE",
        "submission_authority": False,
    }
