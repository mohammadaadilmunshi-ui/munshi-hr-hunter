"""Stable entry point for Candidate-Truth-first Resume Studio V6.

V4/V5 imports remain compatibility markers for long-lived route-contract checks.
Runtime rendering is upgraded to V6's presentation canvas while preserving V5's
strict one-page generation and artifact guards.
"""
from app.resume_studio_page_v4 import render
from app.resume_studio_page_v5 import render as render_v5
from app.resume_studio_page_v6 import render as render_v6

render = render_v6

__all__ = ["render"]
