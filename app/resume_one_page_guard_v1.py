"""Actual-render one-page guard for Native Resume V5.

The earlier writer budget allowed enough content that a DOCX/PDF could be only a
single wrapped bullet away from spilling to page two.  This guard adds a
conservative cross-renderer density budget and then requires the existing parsed
PDF page/layout proof.  A bounded evidence-preserving revision is generated when
the first candidate is too dense or the real renderer reports overflow.

No resume becomes application authority here.  Every revision still goes through
Native Resume V5 Candidate Truth, JD, evidence, do-not-claim, and Stage B guards.
"""
from __future__ import annotations

import math
from typing import Any

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service_v5 as resume_v5

GUARD_VERSION = "native-resume-one-page-guard-v1"
# Deliberately below the old 900-word structural ceiling.  This reserves room
# for Word/Chromium wrapping differences, long URLs, dates, and section headers.
MAX_SAFE_WORDS = 690
MAX_SAFE_SUMMARY_WORDS = 58
MAX_SAFE_BULLET_WORDS = 30
MAX_SAFE_ESTIMATED_LINES = 74
MAX_REPAIR_REVISIONS = 2

_ONE_PAGE_DIRECTION = (
    "Strict one-page cross-renderer requirement: target 560-680 total words, "
    "keep the summary at 45-58 words, keep bullets at 18-30 words, prefer 2-4 "
    "high-value bullets per role, keep skills compact, and omit the lowest-value "
    "supported project/certification before shrinking readability. Preserve every "
    "fact, metric, date, evidence reference, Stage B priority, and do-not-claim rule. "
    "The result must fit one US Letter page in both the generated PDF and a normal "
    "desktop DOCX viewer with no orphan bullet on a second page."
)


def _words(value: Any) -> int:
    return len([part for part in str(value or "").split() if part])


def _wrapped_lines(value: Any, width: int) -> int:
    text = " ".join(str(value or "").split())
    if not text:
        return 0
    return max(1, math.ceil(len(text) / max(1, int(width))))


def density_metrics(document: Any) -> dict[str, Any]:
    """Conservative layout estimate shared by PDF and DOCX preparation.

    This does not replace the real PDF parser.  It reserves vertical room before
    rendering so cross-renderer differences do not create a one-bullet page two.
    """
    doc = resume_v5.v4.v1.ResumeDocument.model_validate(document)
    word_count = resume_v5.v4.v1.document_word_count(doc)
    summary_words = _words(doc.summary.text)
    bullet_words: list[int] = []
    estimated_lines = 0

    # Header + contact + summary heading/body.
    estimated_lines += 4
    estimated_lines += _wrapped_lines(doc.summary.text, 105)

    if doc.experience:
        estimated_lines += 1
        for item in doc.experience:
            estimated_lines += 2 + _wrapped_lines(item.organization + " " + item.location, 105)
            for bullet in item.bullets:
                count = _words(bullet.text)
                bullet_words.append(count)
                estimated_lines += _wrapped_lines(bullet.text, 92)

    if doc.education:
        estimated_lines += 1 + (2 * len(doc.education))
    if doc.projects:
        estimated_lines += 1
        for item in doc.projects:
            estimated_lines += 1
            for bullet in item.bullets:
                count = _words(bullet.text)
                bullet_words.append(count)
                estimated_lines += _wrapped_lines(bullet.text, 92)
    if doc.skills:
        estimated_lines += 1
        skill_text = " | ".join(
            f"{group.label}: {', '.join(group.skills)}" for group in doc.skills
        )
        estimated_lines += _wrapped_lines(skill_text, 108)
    if doc.certifications:
        estimated_lines += 1
        cert_text = " | ".join(
            " - ".join(part for part in (item.name, item.issuer) if part)
            for item in doc.certifications
        )
        estimated_lines += _wrapped_lines(cert_text, 108)

    issues: list[str] = []
    if word_count > MAX_SAFE_WORDS:
        issues.append(f"document_over_{MAX_SAFE_WORDS}_safe_words")
    if summary_words > MAX_SAFE_SUMMARY_WORDS:
        issues.append(f"summary_over_{MAX_SAFE_SUMMARY_WORDS}_safe_words")
    over_bullets = [value for value in bullet_words if value > MAX_SAFE_BULLET_WORDS]
    if over_bullets:
        issues.append(
            f"{len(over_bullets)}_bullet(s)_over_{MAX_SAFE_BULLET_WORDS}_safe_words"
        )
    if estimated_lines > MAX_SAFE_ESTIMATED_LINES:
        issues.append(f"estimated_layout_over_{MAX_SAFE_ESTIMATED_LINES}_lines")

    return {
        "version": GUARD_VERSION,
        "word_count": word_count,
        "summary_words": summary_words,
        "maximum_bullet_words": max(bullet_words, default=0),
        "estimated_lines": estimated_lines,
        "issues": issues,
        "safe": not issues,
    }


def _initial_instruction(user_instruction: str) -> str:
    user = str(user_instruction or "").strip()
    if user:
        return user + "\n\n" + _ONE_PAGE_DIRECTION
    return _ONE_PAGE_DIRECTION


def _repair_instruction(*, issues: list[str], renderer_error: str | None = None) -> str:
    details = ", ".join(issues) if issues else "actual renderer overflow"
    renderer = f" Renderer reported: {renderer_error}" if renderer_error else ""
    return (
        "Create a new compact revision of this exact resume. The current version "
        f"failed the strict one-page guard ({details}).{renderer} "
        + _ONE_PAGE_DIRECTION
        + " Do not add any new claim or evidence. Preserve the strongest JD-matched "
        "evidence and remove/rephrase lower-value supported content first."
    )


def _renderer_repairable(error: Exception) -> bool:
    text = str(error).casefold()
    return any(
        token in text
        for token in (
            "exactly one page",
            "parsed 2 pages",
            "parsed 3 pages",
            "bottom page edge",
            "visibly under-filled",
            "page fit",
        )
    )


def generate_checked(
    *,
    job_id: int,
    rewrite_mode: str,
    instruction: str,
    parent_version_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate, repair if needed, and return only an actual one-page artifact pair."""
    current_parent = parent_version_id
    current_instruction = _initial_instruction(instruction)
    repair_count = 0
    last_error: Exception | None = None

    while True:
        record = resume_v5.generate_resume(
            job_id=int(job_id),
            instruction=current_instruction,
            rewrite_mode=rewrite_mode,
            parent_version_id=current_parent,
        )
        if record.get("stage_b_bound") is not True:
            raise RuntimeError("Native Resume did not return a Stage B-bound version.")

        density = density_metrics(record["document"])
        if density["issues"]:
            if repair_count >= MAX_REPAIR_REVISIONS:
                raise ValueError(
                    "Resume could not satisfy the conservative one-page density budget "
                    "after bounded repair: " + ", ".join(density["issues"])
                )
            current_parent = str(record["version_id"])
            current_instruction = _repair_instruction(issues=list(density["issues"]))
            repair_count += 1
            continue

        try:
            bundle = artifacts.materialize(str(record["version_id"]))
        except (ValueError, RuntimeError) as error:
            last_error = error
            if not _renderer_repairable(error) or repair_count >= MAX_REPAIR_REVISIONS:
                raise
            current_parent = str(record["version_id"])
            current_instruction = _repair_instruction(
                issues=[], renderer_error=str(error)[:400]
            )
            repair_count += 1
            continue

        verification = bundle.get("page_verification") or {}
        if verification.get("status") != "PASS":
            last_error = RuntimeError("The generated resume did not pass page-fit verification.")
            if repair_count >= MAX_REPAIR_REVISIONS:
                raise last_error
            current_parent = str(record["version_id"])
            current_instruction = _repair_instruction(
                issues=[], renderer_error=str(last_error)
            )
            repair_count += 1
            continue

        pdf = bundle.get("pdf") or {}
        if int(pdf.get("page_count") or 0) != 1:
            raise RuntimeError("Checked Native Resume PDF artifact is not exactly one page.")

        record = dict(record)
        diagnostics = dict(record.get("diagnostics") or {})
        diagnostics["one_page_guard"] = {
            **density,
            "repair_revisions": repair_count,
            "actual_pdf_page_count": 1,
            "actual_pdf_page_fit": verification,
            "docx_cross_renderer_safety_budget": "PASS",
        }
        record["diagnostics"] = diagnostics
        bundle = dict(bundle)
        bundle["one_page_guard"] = diagnostics["one_page_guard"]
        return record, bundle
