"""Offline validation of the future container environment source contract."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate() -> list[str]:
    contract = json.loads((ROOT / "config/container_environment_contract.json").read_text(encoding="utf-8"))
    env = contract["environment"]
    project = contract["project"]
    runtime = contract["runtime_targets"]
    hr = contract["hr_agent"]
    errors: list[str] = []
    required = {
        "FASTAPI_HOST": "0.0.0.0", "STREAMLIT_HOST": "0.0.0.0",
        "FASTAPI_BASE_URL": "http://hunter:8000", "N8N_BASE_URL": "http://n8n:5678",
        "OLLAMA_BASE_URL": "http://ollama:11434", "DICE_BROWSER_EXECUTABLE": "/usr/bin/chromium",
        "HR_AGENT_OLLAMA_MODEL": "gemma3:4b", "HR_AGENT_OLLAMA_TIMEOUT_SECONDS": 600,
        "HR_AGENT_PROCESS_TIMEOUT_SECONDS": 240,
    }
    for key, expected in required.items():
        if env.get(key) != expected:
            errors.append(f"{key} must be {expected!r}")
    for key in ("hunter_project_path", "hunter_data_path", "hunter_runtime_path", "hunter_logs_path", "n8n_data_path", "n8n_logs_path"):
        if not project.get(key):
            errors.append(f"missing runtime path: {key}")
    if runtime.get("n8n_version") != "2.22.5" or runtime.get("python_version") != "3.12":
        errors.append("runtime target must be n8n 2.22.5 and Python 3.12")
    if runtime.get("architecture") != {"x86_64": "proven", "arm64_linux": "unproven"}:
        errors.append("architecture proof status is incorrect")
    if not contract["workflow_policy"]["canonical_n8n_workflow_immutable"] or not contract["workflow_policy"]["generated_deployment_workflow_required"]:
        errors.append("workflow immutability/generated-copy policy is incomplete")
    if hr.get("ollama_required_for_hr_agent_scoring") is not True:
        errors.append("HR Agent scoring must explicitly require Ollama")
    lane = hr.get("direct_n8n_ollama_lane", {})
    if lane.get("OLLAMA_ENABLED") is not False or lane.get("OLLAMA_REQUIRED") is not False:
        errors.append("direct n8n Ollama lane must remain optional by default")
    if not contract["future_topology"]["no_postgresql_redis_queue_mode_or_state_migration_in_this_stage"]:
        errors.append("state migration prohibition is missing")
    if not contract["future_topology"]["secrets_runtime_injected_only"]:
        errors.append("runtime-only secrets policy is missing")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("OK: container environment contract is valid")
