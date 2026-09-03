"""Offline structural validation for the immutable n8n portability contract."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def validate() -> list[str]:
    contract = json.loads((ROOT / "config/n8n_portability_contract.json").read_text())
    workflow_path = ROOT / contract["canonical_workflow"]
    workflow_bytes = workflow_path.read_bytes()
    workflow = json.loads(workflow_bytes)
    errors: list[str] = []
    if workflow.get("id") != contract["workflow_id"]:
        errors.append("canonical workflow ID does not match the portability contract")
    if not workflow.get("nodes"):
        errors.append("canonical workflow has no nodes")
    serialized = workflow_bytes.decode("utf-8")
    if hashlib.sha256(workflow_bytes).hexdigest() != contract["canonical_workflow_sha256"]:
        errors.append("canonical workflow SHA-256 does not match the portability contract")
    mappings = contract.get("endpoint_mappings", {})
    for name, mapping in mappings.items():
        actual = serialized.count(mapping["legacy_url"])
        if actual != mapping["canonical_occurrences"]:
            errors.append(f"{name} occurrence count is {actual}, expected {mapping['canonical_occurrences']}")
    if contract.get("canonical_localhost_occurrence_count") != sum(item["canonical_occurrences"] for item in mappings.values()):
        errors.append("canonical localhost occurrence classification total is inconsistent")
    if "/api/hr-agent/score" not in serialized:
        errors.append("HR Agent score route is missing from the canonical workflow")
    if "/api/n8n/status-update" not in serialized:
        errors.append("FastAPI callback route is missing from the canonical workflow")
    if "/api/generate" not in serialized:
        errors.append("Ollama /api/generate route is missing from the canonical workflow")
    if contract.get("canonical_source_policy", {}).get("cloud_workflow_rewrite_forbidden") is not True:
        errors.append("canonical workflow rewrite is not forbidden")
    if contract.get("cloud_contract", {}).get("generated_copy_only") is not True:
        errors.append("deployment policy is not generated-copy-only")
    for key in ("FASTAPI_BASE_URL", "OLLAMA_BASE_URL", "N8N_BASE_URL"):
        if not contract.get("cloud_contract", {}).get("deployment_endpoint_defaults", {}).get(key):
            errors.append(f"missing deployment endpoint default: {key}")
    if contract.get("cloud_contract", {}).get("hr_agent_scoring", {}).get("ollama_required") is not True:
        errors.append("HR Agent Ollama dependency is not explicit")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("OK: n8n portability contract matches canonical workflow")
