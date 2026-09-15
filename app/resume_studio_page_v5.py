"""Resume Studio V5 UI shim with strict actual-render one-page generation.

All existing V4 UI is preserved. Only the internal generation helper is swapped
for the bounded cross-renderer guard while the page is rendering. The Native
Resume quality hotfix is installed immediately before every checked generation so
structured-output reliability and adaptive under-fill/overflow repair also apply
to V6, which delegates to this page.
"""
from __future__ import annotations

from typing import Any

from app import native_resume_quality_hotfix_v1 as quality_hotfix
from app import resume_one_page_guard_v1 as one_page
from app import resume_studio_page_v4 as v4


def _generate_checked(
    *,
    job_id: int,
    rewrite_mode: str,
    instruction: str,
    parent_version_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    quality_hotfix.install()
    return one_page.generate_checked(
        job_id=job_id,
        rewrite_mode=rewrite_mode,
        instruction=instruction,
        parent_version_id=parent_version_id,
    )


def render() -> None:
    quality_hotfix.install()
    original = v4._generate_checked
    v4._generate_checked = _generate_checked
    try:
        v4.render()
    finally:
        v4._generate_checked = original
