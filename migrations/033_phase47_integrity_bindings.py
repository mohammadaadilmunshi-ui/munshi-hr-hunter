"""Additive Phase 4-7 integrity binding migration."""
from app.phase4_job_binding import ensure_schema as ensure_phase4_job_binding_schema


def apply(connection) -> None:
    ensure_phase4_job_binding_schema(connection)
