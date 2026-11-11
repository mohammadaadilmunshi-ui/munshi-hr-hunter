"""Additive Candidate Truth binding sidecars for strengthened Phases 4 and 5."""
from app.phase45_truth_binding import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
