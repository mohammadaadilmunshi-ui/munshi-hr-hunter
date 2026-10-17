"""Guarded, migration-free, one-time production release contract tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/netcup/deploy_code_only_release.sh"
WORKFLOW = ROOT / ".github/workflows/netcup-code-only-deploy.yml"


def test_bash_has_valid_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True, timeout=15)


def test_exact_baseline_and_source_delta_guard():
    content = SCRIPT.read_text()
    assert "3b4e400c3ef9c8f32bcb8b4c037b036ef2a37e0b" in content
    assert '[[ "$old_head" == "$LIVE_BASE" ]]' in content
    assert "git bundle verify" in content
    assert 'git merge-base --is-ancestor "$old_head" "$target"' in content
    assert 'git diff --name-status "$old_head" "$target"' in content
    assert "Unreviewed source change" in content
    assert "Code-only release forbids file deletion or renaming" in content


def test_no_database_migration_or_pretend_full_db_rollback():
    content = SCRIPT.read_text()
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE (image rollback only)" in content
    assert "CODE_ONLY_SCHEMA_MIGRATIONS=NONE" in content
    assert "DATABASE_SCHEMA_UNCHANGED=PASS" in content
    assert "old_schema" in content
    assert "prepare_storage_safe_backup" not in content
    assert "complete_loop_migrate.py" not in content
    assert "CODE_ONLY_FAILURE=inspect_live_SHA_health_and_DB_before_any_retry" in content


def test_swap_and_restore_only_hunter_image():
    content = SCRIPT.read_text()
    assert 'docker tag "$old_image" "$rollback_image"' in content
    assert 'docker tag "$old_image" "$image_tag"' in content
    assert '"${compose[@]}" up -d --no-deps --force-recreate hunter' in content
    assert "old_n_id" in content and "old_o_id" in content
    assert "old_a_id" in content and "old_s_id" in content
    assert "LIVE_DISPATCH_QUEUE=EMPTY" in content


def test_production_action_pinned_source_and_shared_serialization():
    workflow = WORKFLOW.read_text()
    assert "environment: production" in workflow
    assert "group: munshi-netcup-production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "deployment-ci-reusable.yml" in workflow
    assert "NETCUP_KNOWN_HOSTS" in workflow
    assert "NETCUP_SSH_PRIVATE_KEY" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "Checkout exact tested release SHA" in workflow
    assert "/opt/munshi/runtime/code-only" in workflow
    assert "chmod 700" in workflow


def test_bad_sha_stops_locally_without_contacting_production():
    result = subprocess.run(
        [
            "bash", str(SCRIPT), "--commit", "invalid",
            "--branch", "feat/tsenta-connector-completion-20261015",
            "--expected-base", "3b4e400c3ef9c8f32bcb8b4c037b036ef2a37e0b",
            "--bundle", "/tmp/nonexistent",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Exact target SHA required" in result.stderr

def test_actual_reviewed_source_delta_is_strictly_whitelisted():
    import re

    script = SCRIPT.read_text()
    match = re.search(r"allowed_files=\(\n(?P<paths>.*?)\n\)", script, re.DOTALL)
    assert match is not None
    allowlist = {
        line.strip()
        for line in match.group("paths").splitlines()
        if line.strip()
    }
    changed = subprocess.run(
        [
            "git", "-C", str(ROOT), "diff", "--name-only",
            "3b4e400c3ef9c8f32bcb8b4c037b036ef2a37e0b", "HEAD",
        ],
        check=True, capture_output=True, text=True, timeout=10,
    )
    files = {line.strip() for line in changed.stdout.splitlines() if line.strip()}
    assert files
    assert files <= allowlist, f"Unreviewed changes: {files - allowlist}"
    assert not any(
        path.startswith(("scripts/", "migrations/"))
        or path in {"Dockerfile", "compose.yaml", "compose.netcup-shadow.yaml"}
        for path in files
    )
