"""Read-only presentation model for the single-review AutoApply card.

This module translates immutable Native Resume V5 + Stage B records into
candidate-facing review data. It never approves, submits, or mints authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import jd_resume_plan_v1 as planner
from app import native_resume_service_v5 as resumes

_REQUIRED_TYPES = {"SKILL", "TOOL", "DOMAIN_KNOWLEDGE", "PROCESS"}
_REQUIRED_PRIORITIES = {"MUST_HAVE", "CORE_RESPONSIBILITY"}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _document_text(document: Mapping[str, Any]) -> str:
    values: list[str] = [_text((document.get("summary") or {}).get("text"))]
    for group in document.get("skills") or []:
        if isinstance(group, Mapping):
            values.extend(_text(item) for item in group.get("skills") or [])
    for section in ("experience", "projects"):
        for item in document.get(section) or []:
            if not isinstance(item, Mapping):
                continue
            values.extend(
                _text(bullet.get("text"))
                for bullet in item.get("bullets") or []
                if isinstance(bullet, Mapping)
            )
    return "\n".join(value for value in values if value)


def _skills(document: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in document.get("skills") or []:
        if not isinstance(group, Mapping):
            continue
        for raw in group.get("skills") or []:
            value = _text(raw)
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
    return result


def _bullets(document: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for section in ("experience", "projects"):
        for item in document.get(section) or []:
            if not isinstance(item, Mapping):
                continue
            for bullet in item.get("bullets") or []:
                if isinstance(bullet, Mapping):
                    value = _text(bullet.get("text"))
                    if value:
                        result.append(value)
    return result


def _coverage_label(percent: float) -> str:
    if percent >= 80:
        return "Excellent match"
    if percent >= 65:
        return "Strong match"
    if percent >= 50:
        return "Good match"
    return "Developing match"


def _requirement_rows(
    plan: Mapping[str, Any],
    *,
    represented_ids: set[str],
) -> list[dict[str, Any]]:
    refs = [
        dict(item)
        for item in plan.get("requirement_refs") or []
        if isinstance(item, Mapping)
    ]
    required = [
        item
        for item in refs
        if str(item.get("type") or "") in _REQUIRED_TYPES
        and str(item.get("priority") or "") in _REQUIRED_PRIORITIES
        and str(item.get("match_status") or "")
        not in {"UNSUPPORTED", "CONTRADICTED"}
    ]
    if not required:
        priority_ids = {
            str(value)
            for value in plan.get("skills_priority_requirement_ids") or []
        }
        required = [
            item
            for item in refs
            if str(item.get("requirement_id") or "") in priority_ids
        ]
    return [
        {
            "requirement_id": str(item.get("requirement_id") or ""),
            "text": _text(item.get("exact_text")),
            "covered": str(item.get("requirement_id") or "") in represented_ids,
            "priority": str(item.get("priority") or ""),
        }
        for item in required[:8]
        if _text(item.get("exact_text"))
    ]


def build_review_card(
    *,
    resume_record: Mapping[str, Any],
    plan: Mapping[str, Any],
    page_verification: Mapping[str, Any] | None = None,
    parent_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    document = dict(resume_record.get("document") or {})
    diagnostics = dict(resume_record.get("diagnostics") or {})
    stage_b = dict(diagnostics.get("stage_b") or {})
    coverage = dict(stage_b.get("supported_requirement_coverage") or {})

    percent = float(
        coverage.get("percent")
        or diagnostics.get("jd_supported_requirement_coverage_percent")
        or diagnostics.get("jd_term_coverage_percent")
        or 0.0
    )
    represented_ids = {
        str(value)
        for value in coverage.get("represented_requirement_ids") or []
        if str(value)
    }

    resume_text = _document_text(document).casefold()
    supported_terms = [
        _text(value)
        for value in plan.get("supported_jd_terms") or []
        if _text(value)
    ]
    covered_terms = [
        term for term in supported_terms if term.casefold() in resume_text
    ]

    current_bullets = _bullets(document)
    current_skills = _skills(document)
    parent_document = (
        dict(parent_record.get("document") or {}) if parent_record else {}
    )
    parent_bullets = {_norm(value) for value in _bullets(parent_document)}
    parent_skills = {_norm(value) for value in _skills(parent_document)}
    changed_bullets = [
        value for value in current_bullets if _norm(value) not in parent_bullets
    ]
    added_skills = [
        value for value in current_skills if _norm(value) not in parent_skills
    ]
    if not parent_record:
        changed_bullets = current_bullets
        added_skills = current_skills

    verification = dict(page_verification or {})
    one_page = (
        int(verification.get("pdf_pages") or verification.get("page_count") or 0)
        == 1
        and str(verification.get("status") or "PASS").upper() == "PASS"
    )

    return {
        "coverage_percent": round(percent, 1),
        "coverage_label": _coverage_label(percent),
        "represented_requirement_count": int(
            coverage.get("represented_requirement_count")
            or diagnostics.get("jd_supported_requirement_represented_count")
            or 0
        ),
        "supportable_requirement_count": int(
            coverage.get("supportable_requirement_count")
            or diagnostics.get("jd_supported_requirement_count")
            or 0
        ),
        "covered_keyword_count": len(covered_terms),
        "keyword_count": len(supported_terms),
        "required_skills": _requirement_rows(
            plan, represented_ids=represented_ids
        ),
        "changed_bullets": changed_bullets[:12],
        "changed_bullet_count": len(changed_bullets),
        "added_skills": added_skills[:12],
        "added_skill_count": len(added_skills),
        "summary": _text((document.get("summary") or {}).get("text")),
        "one_page": bool(one_page),
        "document": document,
        "is_revision": bool(parent_record),
    }


def load_review_card(
    *,
    resume_version_id: str,
    page_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record = resumes.get_version(str(resume_version_id))
    binding = dict(record.get("stage_b_binding") or {})
    stage_b = dict((record.get("diagnostics") or {}).get("stage_b") or {})
    plan_id = str(binding.get("plan_id") or stage_b.get("plan_id") or "")
    if not plan_id:
        raise LookupError("Tailored resume is missing its Stage B plan binding.")
    plan = planner.get_plan(plan_id)

    parent_record = None
    parent_version_id = str(record.get("parent_version_id") or "")
    if parent_version_id:
        parent_record = resumes.get_version(parent_version_id)

    return build_review_card(
        resume_record=record,
        plan=plan,
        page_verification=page_verification,
        parent_record=parent_record,
    )
