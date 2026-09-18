from __future__ import annotations

import pytest

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_quality_hotfix_v2 as quality
from app import resume_one_page_guard_v1 as one_page


def test_chromium_text_matrix_is_transformed_into_page_space() -> None:
    # Representative Chromium PDF transform: scale to PDF points and flip Y.
    cm = [0.75, 0.0, 0.0, -0.75, 36.0, 774.0]

    # Local text Y=955 is actually ~57.75pt above the page bottom.
    y, size = quality._absolute_text_fragment(
        cm,
        [1.0, 0.0, 0.0, -1.0, 0.0, 955.0],
        12.66,
    )
    assert y == pytest.approx(57.75)
    assert size == pytest.approx(9.495)

    # Raw tm[5]==0 is not a bottom-edge fragment once the page CTM is applied.
    y_zero, _ = quality._absolute_text_fragment(
        cm,
        [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        10.66,
    )
    assert y_zero == pytest.approx(774.0)


def test_reference_like_page_fit_passes_with_realistic_bottom_margin() -> None:
    original_occupied = artifacts.MIN_OCCUPIED_HEIGHT_RATIO
    original_blank = artifacts.MAX_BOTTOM_WHITESPACE_RATIO
    try:
        quality.install()
        # Synthetic page-space fragments model a dense professional one-page
        # layout: ~58pt bottom margin and content extending to ~760pt.
        fragments = [(58.0 + i * 10.0, 10.0) for i in range(71)]
        result = artifacts._page_fit_metrics(page_height=792.0, fragments=fragments)
        assert result["status"] == "PASS"
        assert result["bottom_whitespace_ratio"] == pytest.approx(58.0 / 792.0, abs=0.001)
        assert result["occupied_height_ratio"] > 0.85
    finally:
        artifacts.MIN_OCCUPIED_HEIGHT_RATIO = original_occupied
        artifacts.MAX_BOTTOM_WHITESPACE_RATIO = original_blank


def test_v2_install_uses_reference_sized_one_page_budget() -> None:
    original_words = one_page.MAX_SAFE_WORDS
    original_bullets = one_page.MAX_SAFE_BULLET_WORDS
    original_lines = one_page.MAX_SAFE_ESTIMATED_LINES
    original_direction = one_page._ONE_PAGE_DIRECTION
    original_validator = artifacts._validated_pdf_layout
    try:
        state = quality.install()
        assert state["success"] is True
        assert state["pdf_coordinate_space"] == "PAGE_TRANSFORMED"
        assert state["target_visible_words_min"] == 460
        assert state["target_visible_words_max"] == 540
        assert one_page.MAX_SAFE_WORDS == 575
        assert one_page.MAX_SAFE_BULLET_WORDS == 28
        assert one_page.MAX_SAFE_ESTIMATED_LINES == 68
        assert "460-540" in one_page._ONE_PAGE_DIRECTION
        assert getattr(
            artifacts._validated_pdf_layout,
            "_aadil_transformed_pdf_coordinates_v2",
            False,
        ) is True
        assert state["submission_authority"] is False
    finally:
        one_page.MAX_SAFE_WORDS = original_words
        one_page.MAX_SAFE_BULLET_WORDS = original_bullets
        one_page.MAX_SAFE_ESTIMATED_LINES = original_lines
        one_page._ONE_PAGE_DIRECTION = original_direction
        artifacts._validated_pdf_layout = original_validator
