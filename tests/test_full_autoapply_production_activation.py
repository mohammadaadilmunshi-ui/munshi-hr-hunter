from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_hunter_compose_passes_full_autoapply_activation_contract() -> None:
    source = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    required = {
        "MUNSHI_NATIVE_RESUME_V5_ENABLED",
        "MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED",
        "MUNSHI_APPLICATION_PLAN_V2_ENABLED",
        "MUNSHI_APPLICATION_PLAN_RUNTIME_RESOLUTION_ENABLED",
        "MUNSHI_APPLY_LIVE_HANDOFF_ENABLED",
        "MUNSHI_APPLY_BACKGROUND_PREPARE_ENABLED",
        "MUNSHI_APPLICATION_REVIEW_V2_ENABLED",
        "MUNSHI_PREPARE_WORKER_ADAPTERS_V1C_ENABLED",
        "MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED",
        "MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED",
        "MUNSHI_APPLY_COMMAND_SECRET",
        "MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET",
        "MUNSHI_PRODUCTION_RECEIPT_HMAC_SECRET",
        "MUNSHI_APPLY_INTERNAL_HTTP_ENABLED",
        "MUNSHI_APPLY_INTERNAL_HTTP_BASE_URL",
        "MUNSHI_FINAL_SUBMIT_ENABLED",
    }
    missing = sorted(name for name in required if name not in source)
    assert not missing, missing


def test_hunter_activation_is_phased_and_preserves_n8n_ollama() -> None:
    source = (
        ROOT / "deploy/netcup/activate_full_autoapply_production.sh"
    ).read_text(encoding="utf-8")
    assert "--phase" in source
    assert 'PHASE" == "prepare"' in source
    assert "MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED false" in source
    assert "MUNSHI_APPLY_SUBMIT_AUTHORITY_DELIVERY_ENABLED true" in source
    assert "MUNSHI_FINAL_SUBMIT_ENABLED false" in source
    assert "MUNSHI_FINAL_SUBMIT_ENABLED true" in source
    assert "N8N_RECREATED=NO" in source
    assert "OLLAMA_RECREATED=NO" in source
    assert "HUNTER_TO_APPLY_PRIVATE_BRIDGE_HEALTH=PASS" in source
    assert 'safe.directory=$REPO' in source
    assert "source \"$ENV_FILE\"" not in source


def test_migration_runner_uses_backup_rollback_guarded_segmented_mode() -> None:
    source = (ROOT / "scripts/complete_loop_migrate.py").read_text(encoding="utf-8")
    assert "SEGMENTED_BACKUP_ROLLBACK_GUARDED" in source
    assert "committed_segments" in source
    assert "Migration escaped outer transaction" not in source

def test_hunter_activation_shell_script_parses() -> None:
    subprocess.run(
        ["bash", "-n", str(ROOT / "deploy/netcup/activate_full_autoapply_production.sh")],
        check=True,
    )

def test_production_deploy_uses_storage_safe_rollback_snapshot() -> None:
    source = (ROOT / "deploy/netcup/deploy_production_release.sh").read_text(
        encoding="utf-8"
    )
    assert "--reuse-backup" in source
    assert "RAW_IDENTICAL" in source
    assert "GZIP_V1" in source
    assert "PRODUCTION_DB_COMPRESSED_BACKUP_VERIFY=PASS" in source
    assert "MUNSHI_MAX_COMPRESSED_HUNTER_BACKUP_BYTES" in source
    assert "7516192768" in source
    assert "source.backup(dest)" not in source
    assert "PRODUCTION_DB_BACKUP_REUSED=YES" in source
    assert "ROLLBACK_DB_RAW_SHA256" in source


def test_production_deploy_shell_script_parses() -> None:
    subprocess.run(
        ["bash", "-n", str(ROOT / "deploy/netcup/deploy_production_release.sh")],
        check=True,
    )
