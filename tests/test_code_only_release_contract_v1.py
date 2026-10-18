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
    assert "ed8524545dbcc212dea8748fa6235253600a2682" in content
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
    assert "/opt/munshi/bin/deploy-code-only-release" in workflow
    assert 'cat "$RUNNER_TEMP/munshi-codeonly.bundle"' in workflow
    assert "scp " not in workflow
    assert "mkdir -p '$REMOTE_DIR'" not in workflow


def test_bad_sha_stops_locally_without_contacting_production():
    result = subprocess.run(
        [
            "bash", str(SCRIPT), "--commit", "invalid",
            "--branch", "feat/tsenta-connector-completion-20261015",
            "--expected-base", "ed8524545dbcc212dea8748fa6235253600a2682",
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
            "ed8524545dbcc212dea8748fa6235253600a2682", "HEAD",
        ],
        check=True, capture_output=True, text=True, timeout=10,
    )
    files = {line.strip() for line in changed.stdout.splitlines() if line.strip()}
    assert files
    assert files <= allowlist, f"Unreviewed changes: {files - allowlist}"
    assert not any(
        path.startswith("migrations/")
        or (path.startswith("scripts/") and path != "scripts/linkedin_link_recovery_v7.py")
        or path in {"Dockerfile", "compose.yaml", "compose.netcup-shadow.yaml"}
        for path in files
    )

def test_nonroot_hunter_can_read_ephemeral_test_snapshot_without_source_chmod():
    content = SCRIPT.read_text()
    assert 'test_copy="$(mktemp -d "$ROOT/runtime/codeonly-test-source.XXXXXXXX")"' in content
    assert 'cp -a "$REPO/tests/." "$test_copy/tests/"' in content
    assert 'chmod -R a+rX "$test_copy/tests"' in content
    assert '--mount "type=bind,src=$test_copy/tests,dst=/app/hunter/tests,readonly"' in content
    assert "ISOLATED_TEST_SOURCE=EPHEMERAL_COPY" in content
    assert '[[ -z "$test_copy" ]] || rm -rf -- "$test_copy" || true' in content
    assert 'chmod -R a+rX "$REPO/tests"' not in content
    assert "docker run --rm --network none --read-only" in content
    assert '-p no:cacheprovider' in content



def test_relay_release_acceptance_cannot_skip_atomic_dispatch_tests():
    script = (
        ROOT / "deploy/netcup/deploy_code_only_release.sh"
    ).read_text(encoding="utf-8")
    assert "app/relay_application_dispatch_v1.py" in script
    assert "tests/test_relay_application_dispatch_v1.py" in script
    assert "/app/hunter/tests/test_relay_application_dispatch_v1.py" in script
    assert "/app/hunter/tests/test_relay_inbox_receipt_v1.py" in script
    assert "/app/hunter/tests/test_dashboard_operational_metrics_v1.py" in script
    assert "app/dashboard_operational_metrics_v1.py" in script
    assert "/app/hunter/tests/test_relay_platform_routing_v1.py" in script
    assert "/app/hunter/tests/test_relay_claim_projection_v1.py" in script
    assert "/app/hunter/tests/test_tsenta_learning_monitor_v1.py" in script
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in script
    assert "CODE_ONLY_SCHEMA_MIGRATIONS=NONE" in script
    assert "stop submit-worker" not in script


def test_linkedin_release_cli_is_read_only_and_image_tests_are_required():
    cli = (ROOT / "scripts/linkedin_link_recovery_v7.py").read_text()
    assert "?mode=ro" in cli
    assert "PRAGMA query_only=ON" in cli
    assert "persist_verified_evidence" not in cli
    script = SCRIPT.read_text()
    for name in ("test_linkedin_apply_button_v7.py", "test_linkedin_link_recovery_v7.py",
                 "test_linkedin_verified_url_evidence_v5.py", "test_job_destination_v1.py"):
        assert f"/app/hunter/tests/{name}" in script
