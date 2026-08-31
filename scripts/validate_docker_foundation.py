"""Offline, dependency-free checks for the Stage 6B Docker foundation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SHA256 = "501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f"
REQUIRED_FILES = (
    ".dockerignore", "Dockerfile", "compose.yaml", "docker/hunter-entrypoint.sh",
    "docker/hunter-supervisor.py", "scripts/validate_docker_foundation.py",
    "tests/test_docker_foundation.py", ".github/workflows/docker-foundation.yml",
    "docs/cloud/STAGE6B_DOCKER_FOUNDATION.md", "config/container_environment_contract.json",
    ".env.example",
)


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing required file: {relative}")
    if errors:
        return errors

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    ignore = (root / ".dockerignore").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/docker-foundation.yml").read_text(encoding="utf-8")
    all_docker_source = "\n".join((dockerfile, compose, ignore, workflow))

    for service in ("hunter:", "n8n:", "ollama:"):
        if not re.search(rf"^  {re.escape(service)}\s*$", compose, re.MULTILINE):
            errors.append(f"missing Compose service: {service[:-1]}")
    checks = {
        "n8n exact image": "image: n8nio/n8n:2.22.5" in compose,
        "Hunter Dockerfile": "dockerfile: Dockerfile" in compose,
        "separate Ollama service": "  ollama:" in compose,
        "no PostgreSQL/Redis": not re.search(r"(?im)^\s*(postgres(?:ql)?|redis):", compose),
        "service DNS": all(value in compose for value in ("http://hunter:8000", "http://n8n:5678", "http://ollama:11434")),
        "Dice Chromium": "DICE_BROWSER_EXECUTABLE: /usr/bin/chromium" in compose,
        "HR model": "HR_AGENT_OLLAMA_MODEL: ${HR_AGENT_OLLAMA_MODEL:-gemma3:4b}" in compose,
        "HR timeouts": "HR_AGENT_OLLAMA_TIMEOUT_SECONDS: ${HR_AGENT_OLLAMA_TIMEOUT_SECONDS:-600}" in compose and "HR_AGENT_PROCESS_TIMEOUT_SECONDS: ${HR_AGENT_PROCESS_TIMEOUT_SECONDS:-240}" in compose,
        "no Ollama build pull": not re.search(r"(?im)^(RUN|CMD|ENTRYPOINT).*ollama\s+(pull|run)", dockerfile),
        "loopback ports": all(port in compose for port in ('127.0.0.1:8000:8000', '127.0.0.1:8501:8501', '127.0.0.1:5678:5678')),
        "no public Ollama port": "ports:" not in compose.split("  ollama:", 1)[1].split("\nnetworks:", 1)[0],
        "named volumes": all(f"  {volume}:" in compose for volume in ("hunter_data", "hunter_runtime", "hunter_logs", "n8n_data", "ollama_models")),
    }
    errors.extend(f"failed check: {name}" for name, passed in checks.items() if not passed)
    if re.search(r"(?im)COPY\s+[^\n]*(?:\.env|\.db|\.sqlite|credentials)", dockerfile):
        errors.append("Dockerfile copies a secret or database artifact")
    forbidden = ("Aadil-HR-Hunter", "/Applications/", "~/.n8n", "/Users/", "database.sqlite", "hunter.db")
    if any(token in all_docker_source for token in forbidden):
        errors.append("Docker source contains a forbidden Mac/live-state path")
    if "canonical_hr_hunter_workflow.json" in all_docker_source and "COPY" in dockerfile:
        errors.append("Docker source attempts to copy/rewrite the canonical workflow")
    if "push:" in workflow:
        errors.append("Docker Foundation workflow adds a push trigger")
    canonical = root / "n8n/workflows/canonical_hr_hunter_workflow.json"
    if hashlib.sha256(canonical.read_bytes()).hexdigest() != CANONICAL_SHA256:
        errors.append("canonical n8n workflow SHA-256 changed")
    contract = json.loads((root / "config/container_environment_contract.json").read_text(encoding="utf-8"))
    if contract["runtime_targets"]["architecture"].get("arm64_linux") != "unproven":
        errors.append("ARM64 Linux must remain unproven")
    adapter = root / "integrations/hr_agent/n8n_hr_score.py"
    if "COPY integrations ./integrations" not in dockerfile or not adapter.is_file():
        errors.append("Dockerfile does not include the versioned HR adapter source")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("OK: Docker foundation is valid")
