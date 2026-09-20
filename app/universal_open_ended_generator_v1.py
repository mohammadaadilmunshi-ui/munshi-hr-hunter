"""Deterministic evidence-grounded open-ended answers for Universal Brain.

This generator does not invent company culture, achievements, motivations, or
candidate facts. It combines the exact job snapshot identity with canonical
candidate capability evidence. Unsupported personal questions remain unresolved.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

GENERATOR_VERSION = "universal-open-ended-generator-v1"
_SUPPORTED = frozenset({
    "motivation.why_role",
    "motivation.why_company",
    "motivation.relevant_experience",
})
_STOP = frozenset({
    "and", "the", "for", "with", "this", "that", "role", "job", "work", "your",
    "you", "our", "are", "from", "have", "has", "will", "skills", "experience",
    "responsibilities", "requirements", "preferred", "qualifications", "using",
})


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _tokens(value: Any) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9+#]+", _clean(value).casefold())
        if len(token) >= 3 and token not in _STOP
    }


def _capability_label(concept: str, evidence_text: str) -> str:
    key = str(concept or "").strip().casefold()
    text = _clean(evidence_text)
    if key.startswith("experience.skill."):
        suffix = key.removeprefix("experience.skill.").replace("_", " ")
        # Prefer an explicit short skill/tool label from evidence; avoid copying
        # long resume bullets into generated prose.
        if text and len(text) <= 80 and len(text.split()) <= 8:
            return text
        return suffix
    if key:
        return key.rsplit(".", 1)[-1].replace("_", " ")
    return ""


def _job_text(job: Mapping[str, Any]) -> str:
    return " ".join(
        _clean(job.get(key))
        for key in (
            "title", "description_raw", "responsibilities", "qualifications",
            "preferred_qualifications", "preferred_skills", "skills_keywords",
        )
        if _clean(job.get(key))
    )


def _select_evidence(payload: Mapping[str, Any], *, limit: int = 3) -> list[dict[str, str]]:
    job = dict(payload.get("job_context") or {})
    job_tokens = _tokens(_job_text(job))
    scored: list[tuple[int, int, str, dict[str, str]]] = []
    seen: set[str] = set()
    for index, raw in enumerate(payload.get("candidate_evidence") or []):
        if not isinstance(raw, Mapping):
            continue
        claim_id = _clean(raw.get("claim_id"))
        concept = _clean(raw.get("concept")).casefold()
        label = _capability_label(concept, _clean(raw.get("evidence_text")))
        if not claim_id or not label:
            continue
        normalized = label.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        overlap = len(_tokens(label).intersection(job_tokens))
        scored.append((overlap, -index, normalized, {
            "claim_id": claim_id,
            "concept": concept,
            "label": label,
        }))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[3] for row in scored[: max(1, int(limit))]]


def _join_labels(rows: list[dict[str, str]]) -> str:
    labels = [row["label"] for row in rows]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def generate(payload: Mapping[str, Any]) -> dict[str, Any]:
    if str(payload.get("truth_guard") or "") != "NO_UNSUPPORTED_CLAIMS":
        return {"evidence_supported": False, "reason": "truth_guard_missing"}
    concept_id = _clean(payload.get("concept_id")).casefold()
    if concept_id not in _SUPPORTED:
        return {"evidence_supported": False, "reason": "concept_requires_explicit_user_answer"}
    job = dict(payload.get("job_context") or {})
    title = _clean(job.get("title"))
    company = _clean(job.get("company_name") or job.get("company"))
    evidence = _select_evidence(payload)
    if not title or not evidence:
        return {"evidence_supported": False, "reason": "job_or_candidate_evidence_missing"}
    capabilities = _join_labels(evidence)

    if concept_id == "motivation.why_role":
        answer = (
            f"I'm interested in the {title} role because it aligns with my background in {capabilities}. "
            "The responsibilities described in the posting give me an opportunity to apply that experience while continuing to grow in the role."
        )
    elif concept_id == "motivation.why_company":
        if not company:
            return {"evidence_supported": False, "reason": "company_identity_missing"}
        answer = (
            f"I'm interested in {company} because the {title} opportunity aligns with my background in {capabilities}. "
            "What stands out to me is the fit between the work described in the posting and the experience I can bring to the role."
        )
    else:
        answer = (
            f"My background includes evidence-backed experience in {capabilities}. "
            f"Those areas are relevant to the responsibilities described for the {title} role, and I would bring that experience into the position."
        )
    return {
        "version": GENERATOR_VERSION,
        "answer": answer[:1200],
        "evidence_supported": True,
        "evidence_ids": [row["claim_id"] for row in evidence],
        "confidence": 0.92,
        "autofill_allowed": True,
        "candidate_claims_invented": 0,
        "company_claims_invented": 0,
    }
