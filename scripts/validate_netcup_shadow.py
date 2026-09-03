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
    "scripts/netcup/netcup_hardware_gate.py",
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
    "docs/cloud/STAGE8B_WAITING_FOR_NETCUP.md",
    "docs/cloud/STAGE9_NETCUP_SHADOW_PARITY.md",
    "docs/cloud/STAGE9_NETCUP_ENDURANCE.md",
    "docs/cloud/STAGE10_STATE_MIGRATION_PLAN.md",
    "docs/cloud/STAGE12_CONTROLLED_CUTOVER_PLAN.md",
)

NETCUP_OPERATOR_SHELL_SCRIPTS = (
    "_common.sh",
    "bootstrap_netcup_host.sh",
    "classify_failure.sh",
    "deploy_shadow.sh",
    "local_preapproval_validate.sh",
    "reboot_proof.sh",
    "run_stage8b_stage9.sh",
)
NETCUP_REMOTE_ONLY_SHELL_SCRIPTS = (
    "benchmark_host.sh",
    "endurance_report.sh",
    "endurance_watch.sh",
    "verify_shadow.sh",
)
OPERATOR_PORTABILITY_PATTERNS = (
    (re.compile(r"\$\{[^}\n]*(?:,,|\^\^)[^}\n]*\}"), "Bash 4 case-conversion expansion"),
    (re.compile(r"\$\{[^}\n]*@[A-Za-z][^}\n]*\}"), "Bash 4 parameter transformation"),
    (re.compile(r"(?m)^\s*(?:builtin\s+)?declare\s+-[A-Za-z]*A[A-Za-z]*(?:\s|$)"), "Bash 4 associative array"),
    (re.compile(r"(?m)(?:^|[;&|]\s*)(?:mapfile|readarray|coproc)(?:\s|$)"), "Bash 4 builtin"),
    (re.compile(r"(?m)(?:^|[;&|]\s*)wait\s+-n(?:\s|$)"), "Bash 4 wait -n"),
    (re.compile(r"(?m)^\s*shopt\s+-s\s+[^\n]*\bglobstar\b"), "Bash 4 globstar"),
    (re.compile(r"(?:&>>|\|&)"), "Bash 4 redirection or pipeline operator"),
    (re.compile(r"\bread\s+-[A-Za-z]*[Ni][A-Za-z]*(?:\s|$)"), "Bash 4 read option"),
    (re.compile(r"\b(?:stat\s+-c|readlink\s+-f|date\s+-d|sed\s+-r|grep\s+-P|timeout(?:\s|$)|/usr/bin/time\s+-f)"), "GNU-only command option"),
)


def _local_operator_source(source: str) -> str:
    """Remove explicitly delimited Ubuntu-only heredoc bodies from a wrapper."""
    local_lines: list[str] = []
    in_remote_heredoc = False
    for line in source.splitlines(keepends=True):
        if not in_remote_heredoc and "<<'REMOTE'" in line:
            in_remote_heredoc = True
            local_lines.append(line)
        elif in_remote_heredoc and line.rstrip("\r\n") == "REMOTE":
            in_remote_heredoc = False
            local_lines.append(line)
        elif not in_remote_heredoc:
            local_lines.append(line)
    return "".join(local_lines)


def validate_netcup_operator_portability(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    script_dir = root / "scripts/netcup"
    actual_scripts = {path.name for path in script_dir.glob("*.sh")}
    classified_scripts = set(NETCUP_OPERATOR_SHELL_SCRIPTS) | set(
        NETCUP_REMOTE_ONLY_SHELL_SCRIPTS
    )
    for name in sorted(actual_scripts - classified_scripts):
        errors.append(f"Netcup shell script lacks local/remote classification: {name}")
    for name in sorted(classified_scripts - actual_scripts):
        errors.append(f"classified Netcup shell script is missing: {name}")

    for name in NETCUP_OPERATOR_SHELL_SCRIPTS:
        path = script_dir / name
        if not path.is_file():
            continue
        source = _local_operator_source(path.read_text(encoding="utf-8"))
        for pattern, label in OPERATOR_PORTABILITY_PATTERNS:
            if pattern.search(source):
                errors.append(f"{name} uses unsupported macOS Bash 3.2 construct: {label}")
    return errors


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
    errors.extend(validate_netcup_operator_portability(root))

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
    if "N8N_USER_FOLDER: /home/node" not in base or "N8N_USER_FOLDER: /home/node/.n8n" in base:
        errors.append("n8n user folder must be the parent of the mounted .n8n state directory")
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
    for token in ("x86_64", "CPU_COUNT", "MEM_KIB", "PRESENTED_DISK_BYTES", "ROOT_FREE_BYTES", "ROTA", "netcup_hardware_gate.py", "timedatectl set-timezone America/New_York", "timedatectl show -p Timezone --value", "ufw", "unattended-upgrades", "docker-ce", "/opt/munshi"):
        if token not in bootstrap:
            errors.append(f"bootstrap is missing safety/capability token: {token}")
    if "NVME_COUNT" in bootstrap or "no NVMe device presented" in bootstrap:
        errors.append("bootstrap must not require an NVMe-named guest device")
    target = contract.get("target", {})
    expected_target = {
        "provider_cpu_contract": "AMD EPYC 9645, 8 dedicated cores",
        "provider_storage_contract": "512 GB NVMe SSD",
        "virtualization": "KVM",
        "minimum_memory_gib": 14.5,
        "minimum_presented_disk_bytes": 480000000000,
        "minimum_root_free_bytes": 20000000000,
        "timezone": "America/New_York",
    }
    for key, expected in expected_target.items():
        if target.get(key) != expected:
            errors.append(f"Netcup target contract must set {key}={expected}")
    benchmark = (root / "scripts/netcup/benchmark_host.sh").read_text(encoding="utf-8")
    for token in ("fio_512MiB_bounded", "--size=512M", "--runtime=30"):
        if token not in benchmark:
            errors.append(f"Stage 9 benchmark is missing bounded storage evidence: {token}")
    hardware_gate = (root / "scripts/netcup/netcup_hardware_gate.py").read_text(encoding="utf-8")
    for token in ("Ubuntu 24.04", "AMD_EPYC", "MIN_MEMORY_KIB = 15_204_352", "MIN_PRESENTED_DISK_BYTES = 480_000_000_000", "MIN_ROOT_FREE_BYTES = 20_000_000_000", "PASS_VIRTUAL_BLOCK_CAPACITY"):
        if token not in hardware_gate:
            errors.append(f"hardware gate is missing required classification evidence: {token}")
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
