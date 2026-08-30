"""Offline structural check for the source-controlled n8n portability contract."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def validate() -> list[str]:
    contract = json.loads((ROOT / "config/n8n_portability_contract.json").read_text())
    workflow = json.loads((ROOT / contract["canonical_workflow"]).read_text())
    errors: list[str] = []
    if workflow.get("id") != contract["workflow_id"]:
        errors.append("canonical workflow ID does not match the portability contract")
    if not workflow.get("nodes"):
        errors.append("canonical workflow has no nodes")
    serialized = json.dumps(workflow)
    if "127.0.0.1" not in serialized and "localhost" not in serialized:
        errors.append("expected legacy single-host endpoint assumptions were not found")
    if "/api/n8n/status-update" not in serialized:
        errors.append("FastAPI callback route is missing from the canonical workflow")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("OK: n8n portability contract matches canonical workflow")
