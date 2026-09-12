"""Stable entry point for Candidate-Truth-first Resume Studio V5.

The V4 import remains as a compatibility marker for long-lived route-contract
checks. Runtime rendering is immediately upgraded to V5's strict one-page guard.
"""
from app.resume_studio_page_v4 import render
from app.resume_studio_page_v5 import render as render_v5

render = render_v5

__all__ = ["render"]
