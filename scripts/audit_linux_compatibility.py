"""Deterministic, offline Linux/reproducibility audit for the repository."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY_MODULES = {
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "jobspy": "python-jobspy",
    "pandas": "pandas",
    "playwright": "playwright",
    "pycountry": "pycountry",
    "pydantic": "pydantic",
    "requests": "requests",
    "streamlit": "streamlit",
    "telegram": "python-telegram-bot",
    "uvicorn": "uvicorn",
}
MACOS_COMMAND_PATTERN = re.compile(r"\b(?:launchctl|plutil|osascript)\b|/Applications/|/opt/homebrew/")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _requirements(path: Path) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:==(.+))?$", line)
        if not match:
            result[line] = None
        else:
            result[match.group(1).lower().replace("_", "-")] = match.group(2)
    return result


def _imports() -> set[str]:
    found: set[str] = set()
    for directory in (ROOT / "app", ROOT / "scripts"):
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".", 1)[0])
    return found


def audit() -> list[str]:
    errors: list[str] = []
    declared = _requirements(ROOT / "requirements.txt")
    locked = _requirements(ROOT / "requirements.lock.txt")

    imported_distributions = {
        distribution for module, distribution in THIRD_PARTY_MODULES.items() if module in _imports()
    }
    for distribution in sorted(imported_distributions):
        key = distribution.lower().replace("_", "-")
        if key not in declared:
            errors.append(f"direct import distribution is undeclared: {distribution}")
        if key not in locked:
            errors.append(f"direct import distribution is absent from lock: {distribution}")

    runtime = _json(ROOT / "config/runtime_versions.json")
    production = runtime.get("production_source_environment", {})
    linux = runtime.get("linux_ci_proof_target", {})
    required_production = {"python": "3.12.14", "node": "24.16.0", "npm": "11.13.0", "n8n": "2.22.5", "architecture": "arm64"}
    for key, expected in required_production.items():
        if production.get(key) != expected:
            errors.append(f"runtime contract production {key} must be {expected}")
    if linux.get("architecture") != "x86_64" or linux.get("arm64_linux_status") != "unproven":
        errors.append("runtime contract must identify x86_64 Linux and unproven ARM64 Linux")

    personal_marker = "/Users/" + "aadil"
    tracked_portable = __import__("subprocess").check_output(
        ["git", "ls-files", "-z", "--", "app", "bin", "scripts", "config"],
        cwd=ROOT,
    )
    for raw_path in tracked_portable.split(b"\\0"):
        if not raw_path:
            continue
        path = ROOT / raw_path.decode("utf-8")
        if not path.is_file():
            continue
        if personal_marker in path.read_text(encoding="utf-8", errors="replace"):
            errors.append(
                f"personal absolute path remains in portable source/config: {path.relative_to(ROOT)}"
            )

    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if MACOS_COMMAND_PATTERN.search(text) and "is_macos" not in text:
            errors.append(f"macOS command is not visibly gated: {path.relative_to(ROOT)}")
    for path in (ROOT / "bin").glob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if MACOS_COMMAND_PATTERN.search(text) and "is_macos" not in text and 'uname -s' not in text:
            errors.append(f"macOS command is not visibly gated: {path.relative_to(ROOT)}")

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    if not re.search(r"^OLLAMA_ENABLED=false\s*$", env, re.MULTILINE) or not re.search(r"^OLLAMA_REQUIRED=false\s*$", env, re.MULTILINE):
        errors.append("portable defaults must disable and not require Ollama")

    contract = _json(ROOT / "config/n8n_portability_contract.json")
    workflow_path = ROOT / contract["canonical_workflow"]
    workflow_bytes = workflow_path.read_bytes()
    workflow = json.loads(workflow_bytes)
    if workflow.get("id") != "L1u2xZkgFpi7KEuv":
        errors.append("canonical n8n workflow identity changed")
    digest = hashlib.sha256(workflow_bytes).hexdigest()
    if digest != contract.get("canonical_workflow_sha256"):
        errors.append("canonical n8n workflow JSON was rewritten")
    if contract.get("canonical_source_policy", {}).get("cloud_workflow_rewrite_forbidden") is not True:
        errors.append("n8n portability contract does not forbid cloud workflow rewrites")

    tracked = {line for line in __import__("subprocess").check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()}
    forbidden = [name for name in tracked if name in {".env"} or name.startswith(("data/", ".runtime/", "logs/")) or name.endswith((".db", ".sqlite"))]
    if forbidden:
        errors.append("tracked secret/state artifact: " + ", ".join(sorted(forbidden)))
    return errors


if __name__ == "__main__":
    failures = audit()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("OK: Linux compatibility audit passed")
