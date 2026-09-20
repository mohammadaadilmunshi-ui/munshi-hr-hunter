"""Bounded value-free semantic interpreter for MUNSHI Universal Brain.

The model is never a candidate-truth authority. It may only select a canonical
ontology concept from a closed candidate set using employer/browser structure.
Candidate values, protected answers, credentials, secrets, CAPTCHA material and
submission authority are prohibited from the prompt and output contract.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # noqa: S404
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from app import candidate_ontology_v1 as ontology
from app.database import get_setting
from app.secure_vault import read_secret

SEMANTIC_MODEL_VERSION = "universal-semantic-model-v1"
_SETTINGS_KEY = "product_autoapply_preferences_v1"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_API_MODELS = {
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4-5-20251001",
}
_FORBIDDEN_KEYS = frozenset({
    "value", "display_value", "execution_value", "canonical_answer", "answer",
    "password", "passcode", "otp", "captcha", "secret", "secure_resolver_ref",
    "candidate_value", "candidate_values", "protected_value", "credential",
})
_RESULT_RE = re.compile(r"\{.*\}", re.S)

Runner = Callable[[str, str, str, float, int, str | None], str]


class SemanticModelError(ValueError):
    pass


def _prefs() -> dict[str, Any]:
    raw = dict(get_setting(_SETTINGS_KEY, {}) or {})
    auth_mode = str(raw.get("auth_mode") or "subscription").strip().casefold()
    if auth_mode not in {"subscription", "api"}:
        auth_mode = "subscription"
    model = str(raw.get("model") or "sonnet").strip().casefold()
    if model not in _API_MODELS:
        model = "sonnet"
    try:
        configured_cost = float(raw.get("max_cost_per_application_usd", 1.0))
    except (TypeError, ValueError):
        configured_cost = 1.0
    # One classification should cost only a tiny fraction of a browser run.
    max_cost = max(0.01, min(configured_cost, 0.08))
    return {"auth_mode": auth_mode, "model": model, "max_cost_usd": max_cost}


def _assert_value_free(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().casefold()
            if key in _FORBIDDEN_KEYS and nested not in (None, "", [], {}):
                raise SemanticModelError(f"{path}.{raw_key} contains forbidden candidate/security data")
            _assert_value_free(nested, f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_value_free(nested, f"{path}[{index}]")
    elif isinstance(value, str) and len(value) > 12000:
        raise SemanticModelError(f"{path} contains excessive text")


def _candidate_descriptors(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_candidates = payload.get("ontology_candidates")
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_candidates[:16]:
        if not isinstance(raw, Mapping):
            continue
        concept_id = str(raw.get("concept_id") or "").strip().casefold()
        item = ontology.concept(concept_id)
        if item is None or concept_id in seen:
            continue
        seen.add(concept_id)
        result.append({
            "concept_id": item.concept_id,
            "description": item.description,
            "category": item.category,
            "value_type": item.value_type,
            "sensitivity": item.sensitivity,
            "answer_mode": item.answer_mode,
            "aliases": list(item.aliases)[:24],
            "browser_semantics": list(item.browser_semantics)[:12],
            "retrieval_score": float(raw.get("score") or 0.0),
        })
    return result


def _prompt(payload: Mapping[str, Any]) -> tuple[str, set[str]]:
    _assert_value_free(payload)
    observation = payload.get("observation")
    if not isinstance(observation, Mapping):
        raise SemanticModelError("Semantic observation is missing")
    candidates = _candidate_descriptors(payload)
    if not candidates:
        raise SemanticModelError("No ontology candidates are available for model interpretation")
    allowed = {str(item["concept_id"]) for item in candidates}
    safe = {
        "version": SEMANTIC_MODEL_VERSION,
        "task": "Map an employer form field to exactly one canonical candidate concept, or return null if ambiguous.",
        "rules": [
            "Use only the supplied browser structure and ontology candidate metadata.",
            "Never infer or return the candidate's answer or personal value.",
            "Never downgrade protected/self-ID/credential/post-offer semantics.",
            "Select only a concept_id present in ontology_candidates.",
            "Use null when the wording could plausibly mean multiple concepts.",
            "Confidence must be a number from 0 to 1.",
        ],
        "observation": dict(observation),
        "ontology_candidates": candidates,
        "output_schema": {
            "concept_id": "string|null",
            "confidence": "number",
            "reason_code": "short uppercase identifier",
        },
    }
    prompt = (
        "Return ONLY one JSON object matching output_schema. No markdown and no explanation outside JSON.\n"
        + json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    return prompt, allowed


def _api_runner(prompt: str, model: str, _auth: str, _cost: float, timeout: int, api_key: str | None) -> str:
    if not api_key:
        raise SemanticModelError("Anthropic API credential is unavailable")
    resolved = _API_MODELS.get(model, model)
    try:
        response = httpx.post(
            _ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={"model": resolved, "max_tokens": 220, "messages": [{"role": "user", "content": prompt}]},
            timeout=float(timeout),
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise SemanticModelError("Anthropic semantic interpretation failed") from error
    blocks = body.get("content") if isinstance(body, Mapping) else None
    if not isinstance(blocks, list):
        raise SemanticModelError("Anthropic semantic response is missing content")
    text = "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, Mapping) and block.get("type") == "text"
    ).strip()
    if not text:
        raise SemanticModelError("Anthropic semantic response is empty")
    return text


def _cli_runner(prompt: str, model: str, _auth: str, cost: float, timeout: int, _api_key: str | None) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise SemanticModelError("Claude CLI is unavailable")
    command = [
        claude, "--model", model, "-p", "--permission-mode", "dontAsk",
        "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Read,Grep,Glob",
        "--no-session-persistence", "--max-turns", "1", "--max-budget-usd", str(cost),
        "--output-format", "text", "-",
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        process = subprocess.run(  # noqa: S603
            command, input=prompt, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=timeout, check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SemanticModelError("Claude semantic interpretation could not run") from error
    if process.returncode != 0:
        raise SemanticModelError("Claude semantic interpretation failed")
    return str(process.stdout or "").strip()


def _parse(text: str, allowed: set[str]) -> dict[str, Any]:
    match = _RESULT_RE.search(str(text or "").strip())
    if not match:
        raise SemanticModelError("Semantic model returned no JSON object")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise SemanticModelError("Semantic model returned invalid JSON") from error
    if not isinstance(value, Mapping):
        raise SemanticModelError("Semantic model result must be an object")
    concept_id = str(value.get("concept_id") or "").strip().casefold() or None
    if concept_id is not None and concept_id not in allowed:
        raise SemanticModelError("Semantic model selected a concept outside the closed candidate set")
    try:
        confidence = float(value.get("confidence") or 0.0)
    except (TypeError, ValueError) as error:
        raise SemanticModelError("Semantic model confidence is invalid") from error
    if not 0.0 <= confidence <= 1.0:
        raise SemanticModelError("Semantic model confidence must be between zero and one")
    reason = re.sub(r"[^A-Z0-9_]+", "_", str(value.get("reason_code") or "MODEL_SEMANTIC_INTERPRETATION").upper()).strip("_")[:120]
    return {"concept_id": concept_id, "confidence": confidence, "reason_code": reason or "MODEL_SEMANTIC_INTERPRETATION"}


def interpret(
    payload: Mapping[str, Any],
    *,
    runner: Runner | None = None,
    preferences: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    prompt, allowed = _prompt(payload)
    config = dict(preferences or _prefs())
    auth_mode = str(config.get("auth_mode") or "subscription").casefold()
    model = str(config.get("model") or "sonnet").casefold()
    cost = max(0.01, min(float(config.get("max_cost_usd") or 0.08), 0.08))
    timeout = 25
    api_key = None
    selected_runner = runner
    if selected_runner is None:
        if auth_mode == "api":
            api_key = str(read_secret("autoapply_anthropic_api_key") or "").strip() or None
            selected_runner = _api_runner
        else:
            selected_runner = _cli_runner
    output = selected_runner(prompt, model, auth_mode, cost, timeout, api_key)
    return _parse(output, allowed)


def interpret_fail_closed(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return interpret(payload)
    except (SemanticModelError, RuntimeError, ValueError, OSError):
        return {}
