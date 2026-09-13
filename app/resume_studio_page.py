"""Stable entry point for Candidate-Truth-first Resume Studio V7.

The V4/V5 renderers remain the proven generation and one-page authority. V7
installs the presentation-only Standard/Jake visual layer before V5 renders.
"""
from app.resume_studio_page_v4 import render
from app.resume_studio_page_v5 import render as render_v5
from app.resume_visual_studio_v7 import install_resume_visual_studio_v7


def render_v7() -> None:
    install_resume_visual_studio_v7()
    render_v5()


render = render_v7

__all__ = ["render"]
