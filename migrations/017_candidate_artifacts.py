"""Additive artifact index and explicit master-resume designation history."""
from app.candidate_artifacts import ensure_schema


def apply(connection) -> None:
    ensure_schema(connection)
