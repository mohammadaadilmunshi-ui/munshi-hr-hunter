"""Resume Studio V5 UI shim with strict actual-render one-page generation.

All existing V4 UI is preserved.  Only the internal generation helper is swapped
for the bounded cross-renderer guard while the page is rendering.
"""
from __future__ import annotations

from typing import Any

from app import resume_one_page_guard_v1 as one_page
from app import resume_studio_page_v4 as v4


def _generate_checked(
    *,
    job_id: int,
    rewrite_mode: str,
    instruction: str,
    parent_version_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return one_page.generate_checked(
        job_id=job_id,
        rewrite_mode=rewrite_mode,
        instruction=instruction,
        parent_version_id=parent_version_id,
    )


def render() -> None:
    original = v4._generate_checked
    v4._generate_checked = _generate_checked
    try:
        v4.render()
    finally:
        v4._generate_checked = original
