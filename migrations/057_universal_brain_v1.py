"""Add MUNSHI Universal Brain canonical knowledge and semantic-binding ledgers."""
from __future__ import annotations

import sqlite3

from app import application_semantic_binding_v1, candidate_knowledge_graph_v1


def apply(connection: sqlite3.Connection) -> None:
    candidate_knowledge_graph_v1.ensure_schema(connection)
    application_semantic_binding_v1.ensure_schema(connection)
