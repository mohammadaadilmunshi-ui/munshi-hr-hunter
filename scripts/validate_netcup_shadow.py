"""Offline validation for the Netcup shadow deployment contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SHA256 = "501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f"
REQUIRED_FALSE = (
    "HUNTER_ENABLE_TELEGRAM",
    "HUNTER_ENABLE_DISCOVERY_SCHEDULER",
    "HUNTER_ENABLE_COORDINATOR",
    "TELEGRAM_ENABLED",
    "DISCOVERY_ENABLED",
    "SCHEDULER_ENABLED",
    "COORDINATOR_ENABLED",
    "PRODUCTION_CALLBACKS_ENABLED",
    "PRODUCTION_STATE_IMPORTED",
)
REQUIRED_FILES = (
    ".env.netcup.shadow.example",
    "compose.netcup-shadow.yaml",
    "config/netcup_shadow_environment_contract.json",
    "scripts/netcup/_common.sh",
    "scripts/netcup/bootstrap_netcup_host.sh",
    "scripts/netcup/deploy_shadow.sh",
    "scripts/netcup/verify_shadow.sh",
    "scripts/netcup/benchmark_host.sh",
    "scripts/netcup/endurance_watch.sh",
    "scripts/netcup/endurance_report.sh",
    "scripts/netcup/reboot_proof.sh",
    "scripts/netcup/classify_failure.sh",
    "scripts/netcup/local_preapproval_validate.sh",
    "scripts/netcup/run_stage8b_stage9.sh",
    "docs/cloud/STAGE8B_NETCUP_BASELINE_DIAGNOSTIC.md",
    "docs/cloud/STAGE8B_NETCUP_ENVIRONMENT_CONTRACT.md",
    "docs/cloud/STAGE8B_NETCUP_PREPARATION.md",
    "docs/cloud/STAGE8B_NETCUP_BOOTSTRAP.md",
    "docs/cloud/STAGE8B_NETCUP_BACKUP_DESIGN.md",
    "docs/cloud/STAGE9_NETCUP_SHADOW_PARITY.md",
    "docs/cloud/STAGE9_NETCUP_ENDURANCE.md",
    "docs/cloud/STAGE10_STATE_MIGRATION_PLAN.md",
    "docs/cloud/STAGE12_CONTROLLED_CUTOVER_PLAN.md",
)


def parse_example_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing Stage 8B artifact: {relative}")
    paths = {
        "contract": root / "config/netcup_shadow_environment_contract.json",
        "environment example": root / ".env.netcup.shadow.example",
        "Compose shadow layer": root / "compose.netcup-shadow.yaml",
        "base Compose": root / "compose.yaml",
        "supervisor": root / "docker/hunter-supervisor.py",
        "canonical workflow": root / "n8n/workflows/canonical_hr_hunter_workflow.json",
    }
    for label, path in paths.items():
        if not path.is_file():
            errors.append(f"missing {label}: {path.relative_to(root)}")
    if errors:
        return errors

    contract = json.loads(paths["contract"].read_text(encoding="utf-8"))
    example = parse_example_env(paths["environment example"])
    override = paths["Compose shadow layer"].read_text(encoding="utf-8")
    base = paths["base Compose"].read_text(encoding="utf-8")
    supervisor = paths["supervisor"].read_text(encoding="utf-8")

    expected_urls = {
        "FASTAPI_BASE_URL": "http://hunter:8000",
        "N8N_BASE_URL": "http://n8n:5678",
        "OLLAMA_BASE_URL": "http://ollama:11434",
    }
    for key, expected in expected_urls.items():
        if contract["environment"].get(key) != expected or example.get(key) != expected:
            errors.append(f"{key} must use Docker DNS value {expected}")
        if expected not in base + override:
            errors.append(f"Compose is missing Docker DNS endpoint {expected}")

    for key in REQUIRED_FALSE:
        if contract["environment"].get(key) != "false":
            errors.append(f"contract must set {key}=false")
        if example.get(key) != "false":
            errors.append(f"example must set {key}=false")
        if not re.search(rf"(?m)^\s+{re.escape(key)}:\s+[\"']?false[\"']?\s*$", override):
            errors.append(f"shadow Compose must set {key}=false")

    if contract["environment"].get("CLOUD_SHADOW_MODE") != "true" or example.get("CLOUD_SHADOW_MODE") != "true":
        errors.append("CLOUD_SHADOW_MODE must be true")
    if 'CLOUD_SHADOW_MODE: "true"' not in override:
        errors.append("shadow Compose must enable CLOUD_SHADOW_MODE")
    for service in ("hunter", "n8n", "ollama"):
        block = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  \S|\Z)", override)
        if block is None:
            errors.append(f"shadow Compose is missing {service}")
            continue
        service_text = block.group(1)
        for required in ("platform: linux/amd64", "restart: unless-stopped", "max-size: 10m", 'max-file: "5"'):
            if required not in service_text:
                errors.append(f"{service} is missing {required}")

    if not all(port in base for port in ("HUNTER_FASTAPI_PORT_MAPPING:-127.0.0.1:8000:8000", "HUNTER_STREAMLIT_PORT_MAPPING:-127.0.0.1:8501:8501", "N8N_PORT_MAPPING:-127.0.0.1:5678:5678")):
        errors.append("administrative ports must bind only to loopback")
    if re.search(r"(?m)^\s*-\s*[\"']?(?:0\.0\.0\.0:)?11434:", base + override):
        errors.append("Ollama must not have a host port mapping")
    if any(token in base + override for token in ("/Users/", "Aadil-HR-Hunter", "~/.n8n")):
        errors.append("Compose contains a forbidden Mac production path")
    if "HUNTER_ENABLE_TELEGRAM" not in supervisor or "HUNTER_ENABLE_DISCOVERY_SCHEDULER" not in supervisor or "HUNTER_ENABLE_COORDINATOR" not in supervisor:
        errors.append("shadow controls do not match the real Hunter supervisor")
    if contract["runtime"].get("n8n_version") != "2.22.5" or "n8nio/n8n:2.22.5" not in base:
        errors.append("n8n must be exactly 2.22.5")
    digest = hashlib.sha256(paths["canonical workflow"].read_bytes()).hexdigest()
    if digest != CANONICAL_SHA256 or contract["runtime"].get("canonical_workflow_sha256") != digest:
        errors.append("canonical workflow SHA-256 changed")
    if any(value and "REPLACE_WITH_" not in value for key, value in example.items() if key in {"HUNTER_API_SECRET", "N8N_ENCRYPTION_KEY"}):
        errors.append("environment example contains a non-placeholder secret")

    bootstrap = (root / "scripts/netcup/bootstrap_netcup_host.sh").read_text(encoding="utf-8")
    deploy = (root / "scripts/netcup/deploy_shadow.sh").read_text(encoding="utf-8")
    verify = (root / "scripts/netcup/verify_shadow.sh").read_text(encoding="utf-8")
    endurance = (root / "scripts/netcup/endurance_watch.sh").read_text(encoding="utf-8")
    operator = (root / "scripts/netcup/run_stage8b_stage9.sh").read_text(encoding="utf-8")
    for token in ("Ubuntu", "x86_64", "CPU_COUNT", "MEM_KIB", "NVME_COUNT", "ufw", "unattended-upgrades", "docker-ce", "/opt/munshi"):
        if token not in bootstrap:
            errors.append(f"bootstrap is missing safety/capability token: {token}")
    for token in ("git clone", "git -C", "render_n8n_deployment_workflow.py", "gemma3:4b", "PRODUCTION_STATE_IMPORTED=false", "HUNTER_ENABLE_TELEGRAM=false"):
        if token not in deploy:
            errors.append(f"deployment is missing required behavior: {token}")
    for token in ("GO_STAGE9_CLOUD_SHADOW", "NO_GO_STAGE9_CLOUD_SHADOW", "Playwright", "n8n version 2.22.5", "canonical workflow SHA", "production_mac_mutations"):
        if token not in verify:
            errors.append(f"verifier is missing required proof/result: {token}")
    for duration in ("1|6|24|48|72", "OOM_KILL", "UNEXPLAINED_RESTART", "DATABASE_INTEGRITY_FAILURE", "UNCONTROLLED_LOG_GROWTH"):
        if duration not in endurance:
            errors.append(f"endurance watcher is missing: {duration}")
    for option in ("--prepare-only", "--bootstrap", "--deploy", "--verify", "--benchmark", "--reboot-proof", "--endurance-hours", "--report", "--cleanup-shadow", "WAITING_FOR_NETCUP_PROVISIONING"):
        if option not in operator:
            errors.append(f"operator is missing option/result: {option}")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("OK: Netcup shadow environment and Compose contracts are valid")
