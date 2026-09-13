"""Stable entry point for Candidate-Truth-first Resume Studio V6.

The V4 and V5 imports remain as compatibility markers for long-lived route
contracts. V5 still owns strict one-page generation; V6 installs a presentation-
only visual editor/preview layer before V5 renders.
"""
from app.resume_studio_page_v4 import render
from app.resume_studio_page_v5 import render as render_v5
from app.resume_visual_studio_v6 import install_resume_visual_studio_v6


def render_v6() -> None:
    install_resume_visual_studio_v6()
    render_v5()


render = render_v6

__all__ = ["render"]
