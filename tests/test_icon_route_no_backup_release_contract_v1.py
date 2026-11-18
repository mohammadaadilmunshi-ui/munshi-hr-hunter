"""Exact-SHA no-backup deploy gate for the post-release icon routing hotfix."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/netcup/deploy_icon_route_no_backup_release_v1.sh"
GATEWAY = ROOT / "deploy/netcup/github_deploy_gateway.sh"
ADMIN = ROOT / "deploy/netcup/install_icon_route_no_backup_gate_v1.sh"
WORKFLOW = ROOT / ".github/workflows/netcup-code-only-deploy.yml"

LIVE = "b910c4976f3a3c81f57cb89950a6314cdc6b8048"
TARGET = "854fcc01bc80f3a209195274d6e6d099c02d8e66"
DEPLOY_HASH = "91e485f154c8d89654c2fed6243349055e1ee2079e4253d5260741a4034e40c0"
GATE_HASH = "841935f53dc4c6f6ca9a65eb75ff189d2d4a691c3bfca004e9d787cda1361b83"
OLD_GATE_HASH = "7a6c0f1e786f2c81722164b972565a5e400bfe1a615d86b6e4b204f975632798"
EXPECTED = {
    ".github/workflows/netcup-code-only-deploy.yml",
    "app/device_auth.py",
    "deploy/netcup/deploy_crown_no_backup_release_v1.sh",
    "deploy/netcup/github_deploy_gateway.sh",
    "deploy/netcup/install_crown_no_backup_gate_v1.sh",
    "docs/MUNSHI_CROWN_NO_BACKUP_RELEASE_20261117.md",
    "tests/test_code_only_release_contract_v1.py",
    "tests/test_crown_no_backup_release_contract_v1.py",
    "tests/test_dashboard_brand_asset_route_v1.py",
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def test_approved_application_hotfix_is_ancestor_of_release_branch():
    assert _git("rev-parse", LIVE) == LIVE
    assert _git("rev-parse", TARGET) == TARGET
    assert subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", LIVE, TARGET],
        check=False,
    ).returncode == 0
    assert set(_git("diff", "--name-only", LIVE, TARGET).splitlines()) == EXPECTED
    assert not any(
        name.startswith(("static/", "config/", "migrations/", "docker/", "app/database"))
        or name in ("Dockerfile", "compose.yaml", "compose.netcup-shadow.yaml")
        for name in EXPECTED
    )


def test_exact_source_allowlist_is_not_a_general_deploy_bypass():
    source = DEPLOY.read_text()
    assert f"LIVE_BASE={LIVE}" in source
    assert f'[[ "$target" == "{TARGET}" ]]' in source
    assert '[[ "$old_head" == "$LIVE_BASE" ]]' in source
    assert 'git diff --name-status "$old_head" "$target"' in source
    match = re.search(r"allowed_files=\(\n(?P<files>.*?)\n\)", source, re.DOTALL)
    assert match
    assert set(match.group("files").split()) == EXPECTED
    assert '[[ "$changed" == 9 ]]' in source
    assert "Code-only release forbids file deletion or renaming" in source
    assert 'allowed_file "$path" || block' in source
    assert hashlib.sha256(DEPLOY.read_bytes()).hexdigest() == DEPLOY_HASH


def test_no_database_copy_or_migration_and_previous_image_rollback():
    source = DEPLOY.read_text()
    assert "prepare_storage_safe_backup" not in source
    assert "complete_loop_migrate.py" not in source
    assert "CODE_ONLY_SCHEMA_MIGRATIONS=NONE" in source
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in source
    assert "DATABASE_SCHEMA_UNCHANGED=PASS" in source
    assert 'docker tag "$old_image" "$rollback_image"' in source
    assert 'docker tag "$old_image" "$image_tag"' in source
    assert '"${compose[@]}" up -d --no-deps --force-recreate hunter' in source
    assert "LIVE_DISPATCH_QUEUE=EMPTY" in source
    for variable in ("old_n_id", "old_o_id", "old_a_id", "old_p_id", "old_s_id", "old_mount", "old_schema"):
        assert variable in source


def test_ssh_gateway_accepts_only_the_exact_new_verbatim_command():
    source = GATEWAY.read_text()
    assert f'--commit {TARGET} --branch feat/tsenta-connector-completion-20261015' in source
    assert 'if [[ "$ORIGINAL" == "$ICON_ROUTE_COMMAND" ]]' in source
    assert f'--expected-base {LIVE}' in source
    assert "request rejected by MUNSHI GitHub deployment gateway" in source
    assert f'--commit {LIVE} --branch feat/tsenta-connector-completion-20261015' in source
    assert hashlib.sha256(GATEWAY.read_bytes()).hexdigest() == GATE_HASH


def test_workflow_has_exact_source_checksum_and_protected_ci():
    source = WORKFLOW.read_text()
    assert "icon-route-20261118" in source
    assert f'[[ "$RELEASE_SHA" == "{TARGET}" ]]' in source
    assert f'[[ "$EXPECTED_LIVE" == "{LIVE}" ]]' in source
    assert DEPLOY_HASH in source
    assert "environment: production" in source
    assert "cancel-in-progress: false" in source
    assert "deployment-ci-reusable.yml" in source
    assert "NETCUP_SSH_PRIVATE_KEY" in source and "NETCUP_KNOWN_HOSTS" in source
    assert "StrictHostKeyChecking=yes" in source
    assert "deploy-icon-route-no-backup-release" in source


def test_root_only_installer_preserves_existing_gateway_and_checks_hashes():
    source = ADMIN.read_text()
    assert '[[ "$(id -u)" == 0 ]]' in source
    for sha in (DEPLOY_HASH, GATE_HASH, OLD_GATE_HASH):
        assert sha in source
    assert 'git -c safe.directory="$ROOT/repo"' in source
    assert "git config --global" not in source
    assert "DATABASE_BACKUP=NONE" in source
    assert "DATABASE_MIGRATION=NONE" in source
    assert "APP_NOT_RESTARTED=YES" in source
    assert "DB_PATH" not in source and "docker compose" not in source
    for path in (DEPLOY, GATEWAY, ADMIN):
        subprocess.run(["bash", "-n", str(path)], check=True)
