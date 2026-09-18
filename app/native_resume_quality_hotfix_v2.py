"""Native Resume V2 runtime quality repair: transformed PDF coordinates + tighter one-page envelope.

This module is preparation-only. It changes neither Candidate Truth nor application,
ATS/browser, n8n, outreach, or submission authority.

Why it exists
-------------
Chromium-generated PDFs commonly apply a page-level current transformation matrix
(CTM) that scales and flips the Y axis. PyPDF's visitor callback therefore exposes
text-matrix coordinates (tm) that are local to that CTM. Treating ``tm[5]`` as an
absolute page Y coordinate can misclassify perfectly normal text as sitting at the
bottom edge (many link/text fragments legitimately report ``tm[5] == 0``).

V1 also targeted 520-640 visible words. A verified one-page reference layout is
closer to ~500 words, so this repair narrows the professional envelope without
shrinking typography or fabricating filler.
"""
from __future__ import annotations

import io
import math
from typing import Any

from pypdf import PdfReader

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_quality_hotfix_v1 as v1
from app import resume_one_page_guard_v1 as one_page

VERSION = "native-resume-quality-hotfix-v2"
MARKER = "AADIL_NATIVE_RESUME_PDF_COORDINATE_FIT_V2"

TARGET_VISIBLE_WORDS_MIN = 460
TARGET_VISIBLE_WORDS_MAX = 540
MAX_PROFESSIONAL_WORDS = 575
MAX_PROFESSIONAL_BULLET_WORDS = 28
MAX_PROFESSIONAL_ESTIMATED_LINES = 68

_PROFESSIONAL_ONE_PAGE_DIRECTION = (
    "Professional one-page requirement: create a polished US Letter resume that "
    "uses the page efficiently without looking crowded. Aim for roughly 460-540 "
    "visible words when the supplied evidence supports that much content, a 40-55 "
    "word summary, concise 18-28 word bullets, and normally 2-3 strongest relevant "
    "bullets per role. Keep projects to the strongest supported evidence and keep "
    "skills/certifications compact. Preserve readable margins, normal professional "
    "spacing, and approximately 10pt-or-better body readability. Never pad with "
    "filler, duplicate claims, invented facts, unsupported skills, unsupported "
    "metrics, or readability shrinkage. The final PDF must be exactly one page."
)


def _absolute_text_fragment(
    cm: Any,
    tm: Any,
    font_size: Any,
) -> tuple[float, float]:
    """Return absolute PDF Y and effective font size from PyPDF visitor matrices.

    PyPDF exposes the current transformation matrix (cm) and text matrix (tm)
    separately. A Chromium page typically uses a CTM such as
    ``[0.75, 0, 0, -0.75, x, y]``. The page-space Y coordinate is therefore the
    Y component of ``tm * cm``, not raw ``tm[5]``.
    """
    try:
        if len(cm) < 6 or len(tm) < 6:
            raise ValueError
        a, b, c, d, e, f = (float(value) for value in cm[:6])
        tx = float(tm[4])
        ty = float(tm[5])
        size = abs(float(font_size or 0))
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("Invalid PDF text transform.") from exc

    absolute_y = (tx * b) + (ty * d) + f
    # Vertical scale magnitude for rotated/scaled CTMs. Fall back to 1.0 for a
    # degenerate matrix so page-fit code can still apply its minimum font proxy.
    vertical_scale = math.hypot(b, d) or 1.0
    effective_font_size = size * vertical_scale
    return absolute_y, effective_font_size


def _validated_pdf_layout_transformed(data: bytes) -> dict[str, Any]:
    """Verify a one-page PDF using page-space coordinates, not raw text matrices."""
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if len(reader.pages) != 1:
            raise ValueError(
                "Native Resume V5 PDF must be exactly one page; "
                f"parsed {len(reader.pages)} pages."
            )
        page = reader.pages[0]
        page_height = float(page.mediabox.height)
        fragments: list[tuple[float, float]] = []

        def visitor_text(text, cm, tm, _font_dict, font_size):
            if not str(text or "").strip():
                return
            try:
                y, size = _absolute_text_fragment(cm, tm, font_size)
            except ValueError:
                return
            if 0 <= y <= page_height:
                fragments.append((y, size))

        page.extract_text(visitor_text=visitor_text)
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Native Resume V5 PDF renderer returned an invalid document."
        ) from exc

    return artifacts._page_fit_metrics(
        page_height=page_height,
        fragments=fragments,
    )


def install() -> dict[str, Any]:
    """Install V1 reliability plus corrected PDF geometry and reference-fit budget."""
    prior = v1.install()

    # V1's adaptive repair function reads this module global dynamically, so
    # updating the V1 direction keeps both under-fill and overflow repairs aligned
    # with the tighter reference envelope without duplicating truth-safety logic.
    v1._PROFESSIONAL_ONE_PAGE_DIRECTION = _PROFESSIONAL_ONE_PAGE_DIRECTION
    one_page._ONE_PAGE_DIRECTION = _PROFESSIONAL_ONE_PAGE_DIRECTION
    one_page.MAX_SAFE_WORDS = MAX_PROFESSIONAL_WORDS
    one_page.MAX_SAFE_BULLET_WORDS = MAX_PROFESSIONAL_BULLET_WORDS
    one_page.MAX_SAFE_ESTIMATED_LINES = MAX_PROFESSIONAL_ESTIMATED_LINES

    if not getattr(
        artifacts._validated_pdf_layout,
        "_aadil_transformed_pdf_coordinates_v2",
        False,
    ):
        _validated_pdf_layout_transformed._aadil_transformed_pdf_coordinates_v2 = True  # type: ignore[attr-defined]
        _validated_pdf_layout_transformed._aadil_prior_validator = artifacts._validated_pdf_layout  # type: ignore[attr-defined]
        artifacts._validated_pdf_layout = _validated_pdf_layout_transformed

    return {
        **prior,
        "success": True,
        "version": VERSION,
        "marker": MARKER,
        "pdf_coordinate_space": "PAGE_TRANSFORMED",
        "target_visible_words_min": TARGET_VISIBLE_WORDS_MIN,
        "target_visible_words_max": TARGET_VISIBLE_WORDS_MAX,
        "max_safe_words": one_page.MAX_SAFE_WORDS,
        "max_safe_bullet_words": one_page.MAX_SAFE_BULLET_WORDS,
        "max_safe_estimated_lines": one_page.MAX_SAFE_ESTIMATED_LINES,
        "submission_authority": False,
    }
