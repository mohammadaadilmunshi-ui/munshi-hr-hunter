"""One-time, no-database-backup crown deployment safety contract."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/netcup/deploy_crown_no_backup_release_v1.sh"
GATEWAY = ROOT / "deploy/netcup/github_deploy_gateway.sh"
ADMIN = ROOT / "deploy/netcup/install_crown_no_backup_gate_v1.sh"
WORKFLOW = ROOT / ".github/workflows/netcup-code-only-deploy.yml"

LIVE = "31c6c5b7c2942b7c0416ce1dc2f0dd11898f8d5a"
TARGET = "b910c4976f3a3c81f57cb89950a6314cdc6b8048"
DEPLOY_HASH = "10a5a1352273d18800522ea0073281bb8ff4a47ded2f389a484ff35a38ef5276"
GATE_HASH = "7a6c0f1e786f2c81722164b972565a5e400bfe1a615d86b6e4b204f975632798"

EXPECTED = {
    ".streamlit/config.toml", "Dockerfile",
    "app/appearance_theme_v1.py", "app/dashboard.py", "app/device_auth.py",
    "scripts/generate_munshi_icons.py", "scripts/package_streamlit_theme.py",
    "scripts/verify_uiux_runtime.py", "static/apple-touch-icon.png",
    "static/favicon-dark.png", "static/favicon-light.png", "static/favicon.ico",
    "static/icon-192.png", "static/icon-512.png",
    "static/icon-maskable-512.png", "static/manifest.json",
    "tests/test_device_auth.py", "tests/test_loading_system_appearance_v1.py",
    "tests/test_munshi_brand_icons_v1.py", "tests/test_product_ui_v22.py",
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def test_target_is_actual_approved_brand_release():
    assert _git("rev-parse", TARGET) == TARGET
    assert subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", LIVE, TARGET],
        check=False,
    ).returncode == 0
    assert set(_git("diff", "--name-only", LIVE, TARGET).splitlines()) == EXPECTED
    assert not any(
        path.startswith(("migrations/", "config/", "app/database", "docker/"))
        for path in EXPECTED
    )


def test_only_docker_change_copies_curated_static_assets():
    diff = _git("diff", "--unified=0", LIVE, TARGET, "--", "Dockerfile")
    added = [x for x in diff.splitlines() if x.startswith("+") and not x.startswith("+++")]
    removed = [x for x in diff.splitlines() if x.startswith("-") and not x.startswith("---")]
    assert added == ["+COPY static ./static"]
    assert not removed
    toml = _git("diff", "--unified=0", LIVE, TARGET, "--", ".streamlit/config.toml")
    assert [x for x in toml.splitlines() if x.startswith("+") and not x.startswith("+++")] == [
        "+[server]", "+enableStaticServing = true", "+"
    ]
    assert not [x for x in toml.splitlines() if x.startswith("-") and not x.startswith("---")]


def test_release_script_is_pinned_and_whitelisted():
    script = DEPLOY.read_text()
    assert f"LIVE_BASE={LIVE}" in script
    assert f'[[ "$target" == "{TARGET}" ]]' in script
    assert '[[ "$old_head" == "$LIVE_BASE" ]]' in script
    assert 'git diff --name-status "$old_head" "$target"' in script
    match = re.search(r"allowed_files=\(\n(?P<files>.*?)\n\)", script, re.DOTALL)
    assert match
    assert set(match.group("files").split()) == EXPECTED
    assert 'allowed_file "$path" || block' in script
    assert "Code-only release forbids file deletion or renaming" in script
    assert hashlib.sha256(DEPLOY.read_bytes()).hexdigest() == DEPLOY_HASH


def test_no_database_backup_and_preserve_other_services():
    script = DEPLOY.read_text()
    assert "prepare_storage_safe_backup" not in script
    assert "complete_loop_migrate.py" not in script
    assert "DATABASE_SCHEMA_UNCHANGED=PASS" in script
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in script
    assert 'docker tag "$old_image" "$rollback_image"' in script
    assert '"${compose[@]}" up -d --no-deps --force-recreate hunter' in script
    for key in ("old_n_id", "old_o_id", "old_a_id", "old_p_id", "old_s_id", "old_schema", "old_mount"):
        assert key in script
    assert "LIVE_DISPATCH_QUEUE=EMPTY" in script
    assert "active_submits" in script


def test_gateway_exact_match_additive_without_legacy_access_changes():
    script = GATEWAY.read_text()
    assert 'CROWN_ONLY_COMMAND="/opt/munshi/bin/deploy-crown-no-backup-release --commit ' + TARGET in script
    assert 'if [[ "$ORIGINAL" == "$CROWN_ONLY_COMMAND" ]]' in script
    assert f'--expected-base {LIVE}' in script
    assert "CODE_ONLY_BASE=\"cc9dbf29428551a258bbbb619b99071c445ff055\"" in script
    assert 'echo "request rejected by MUNSHI GitHub deployment gateway"' in script
    gateway_hash = hashlib.sha256(GATEWAY.read_bytes()).hexdigest()
    if 'ICON_ROUTE_COMMAND="' in script:
        # The second approved gate adds exactly one pinned icon-404 command,
        # retaining every previous crown and legacy gateway route.
        assert gateway_hash == "d5264b5ff54eb3eb4d7f21c7b169d03415db44e6e80b8841b9c2e5db7d7d8c59"
    else:
        assert gateway_hash == GATE_HASH


def test_workflow_requires_explicit_one_time_kind_and_protected_gate():
    text = WORKFLOW.read_text()
    assert "crown-20261117" in text
    assert f'[[ "$RELEASE_SHA" == "{TARGET}" ]]' in text
    assert f'[[ "$EXPECTED_LIVE" == "{LIVE}" ]]' in text
    assert DEPLOY_HASH in text
    assert "environment: production" in text
    assert "cancel-in-progress: false" in text
    assert "deployment-ci-reusable.yml" in text
    assert "NETCUP_SSH_PRIVATE_KEY" in text and "NETCUP_KNOWN_HOSTS" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "deploy-crown-no-backup-release" in text


def test_root_console_installer_is_pinned_and_refuses_nonroot():
    script = ADMIN.read_text()
    assert '[[ "$(id -u)" == 0 ]]' in script
    # Root may inspect only this specific munshi-owned repo; no global
    # safe.directory mutation and no wildcard ownership bypass.
    assert script.count('git -c safe.directory="$ROOT/repo" -C "$ROOT/repo"') == 2
    assert 'git config --global' not in script
    assert 'safe.directory=*' not in script
    assert 'live_head="$(git -c safe.directory=' in script
    assert 'live_dirty="$(git -c safe.directory=' in script
    assert 'BLOCKED: unable to verify live commit' in script
    assert 'BLOCKED: unable to verify live working tree' in script
    for checksum in (DEPLOY_HASH, GATE_HASH):
        assert checksum in script
    assert "12bec74f157de85e91c02764ba648d4b8cdd35785412e8baaf0de0f16fa4df8a" in script
    assert "DB_PATH" not in script and "docker compose" not in script
    assert "DATABASE_BACKUP=NONE" in script
    for path in (DEPLOY, GATEWAY, ADMIN):
        subprocess.run(["bash", "-n", str(path)], check=True)
