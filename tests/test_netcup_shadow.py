from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app import database
from scripts.netcup.netcup_hardware_gate import (
    classify_hardware,
    parse_forensic_report,
)
from scripts.validate_netcup_shadow import (
    CANONICAL_SHA256,
    OPERATOR_PORTABILITY_PATTERNS,
    validate,
    validate_netcup_operator_portability,
)

ROOT = Path(__file__).resolve().parents[1]


def test_netcup_shadow_contract_is_valid() -> None:
    assert validate(ROOT) == []


def test_canonical_workflow_remains_immutable() -> None:
    canonical = ROOT / "n8n/workflows/canonical_hr_hunter_workflow.json"
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == CANONICAL_SHA256


def test_example_contains_placeholders_only() -> None:
    example = (ROOT / ".env.netcup.shadow.example").read_text(encoding="utf-8")
    assert "REPLACE_WITH_SYNTHETIC_SHADOW_SECRET" in example
    assert "REPLACE_WITH_SYNTHETIC_SHADOW_KEY" in example
    assert "TELEGRAM_ENABLED=false" in example
    assert "PRODUCTION_STATE_IMPORTED=false" in example


def test_netcup_operator_shell_is_macos_bash_32_portable() -> None:
    assert validate_netcup_operator_portability(ROOT) == []


@pytest.mark.parametrize(
    "source",
    (
        "lower=${host,,}",
        "upper=${host^^}",
        "declare -A values",
        "mapfile -t values < input",
        "readarray -t values < input",
    ),
)
def test_operator_portability_patterns_reject_known_bash_4_constructs(
    source: str,
) -> None:
    assert any(pattern.search(source) for pattern, _ in OPERATOR_PORTABILITY_PATTERNS)


def test_host_validation_is_case_insensitive_under_macos_bash_32() -> None:
    common = ROOT / "scripts/netcup/_common.sh"
    rejected = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; netcup_validate_host LOCALHOST',
            "netcup-host-validation",
            str(common),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert "refusing localhost" in rejected.stderr
    assert "bad substitution" not in rejected.stderr

    accepted = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'source "$1"; netcup_validate_host Example.COM',
            "netcup-host-validation",
            str(common),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr


def test_shared_checksum_helper_has_a_macos_compatible_path() -> None:
    common = ROOT / "scripts/netcup/_common.sh"
    completed = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'PATH=/usr/bin:/bin; source "$1"; netcup_canonical_sha "$2"',
            "netcup-checksum",
            str(common),
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == CANONICAL_SHA256


def test_real_netcup_vda_rota1_fixture_passes_storage_classification() -> None:
    fixture = ROOT / "tests/fixtures/netcup_real_host_vda_rota1_forensic.txt"
    fixture_text = fixture.read_text(encoding="utf-8")
    facts = parse_forensic_report(fixture_text)
    result = classify_hardware(facts)

    assert facts["ROOT_SOURCE"] == "/dev/vda3"
    assert "vda" in fixture_text
    assert " 1    disk" in fixture_text
    assert result.storage_classification == "PASS_VIRTUAL_BLOCK_CAPACITY"
    assert result.cpu_model_evidence == "AMD_EPYC"
    assert result.passed


def test_storage_gate_does_not_require_nvme_name_or_nonrotational_flag() -> None:
    facts = parse_forensic_report(
        (ROOT / "tests/fixtures/netcup_real_host_vda_rota1_forensic.txt").read_text(
            encoding="utf-8"
        )
    )
    facts["ROOT_SOURCE"] = "/dev/vda3"
    facts["ROTA"] = "1"
    facts["NVME_COUNT"] = "0"

    result = classify_hardware(facts)
    assert result.storage_classification == "PASS_VIRTUAL_BLOCK_CAPACITY"
    assert result.passed


def test_fresh_database_has_portable_runtime_relations(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "fresh-shadow.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    database.initialize_database()
    connection = sqlite3.connect(path)
    try:
        relations = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        assert {
            "source_random_schedule",
            "source_runtime_truth_v1",
            "telegram_delivery_claims",
        } <= relations
        assert connection.execute(
            "SELECT COUNT(*) FROM source_runtime_truth_v1"
        ).fetchone()[0] >= 0
    finally:
        connection.close()

def test_shadow_verifier_uses_configured_image_reference_and_runtime_arch() -> None:
    verifier = (
        ROOT / "scripts/netcup/verify_shadow.sh"
    ).read_text(encoding="utf-8")

    assert "docker inspect -f '{{.Config.Image}}' \"$cid\"" in verifier
    assert (
        "docker image inspect -f '{{.Architecture}}' \"$image_ref\""
        in verifier
    )
    assert 'exec -T "$service" uname -m </dev/null' in verifier
    assert "docker inspect -f '{{.Image}}' \"$cid\"" not in verifier
