"""Ensure production's existing forced-command SSH gateway remains restrictive."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[1] / "deploy/netcup/github_deploy_gateway.sh"
WRAPPER = Path(__file__).resolve().parents[1] / "deploy/netcup/deploy_code_only_release.sh"
SHA = "a" * 40
BRANCH = "feat/tsenta-connector-completion-20261015"
GOOD = f"/opt/munshi/bin/deploy-code-only-release --commit {SHA} --branch {BRANCH}"


def _gateway(command: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SSH_ORIGINAL_COMMAND": command}
    return subprocess.run(
        ["bash", str(GATEWAY)], env=env,
        check=False, capture_output=True, text=True, timeout=10,
    )


def test_existing_original_production_verbs_preserved():
    text = GATEWAY.read_text()
    assert 'exec "$PRODUCTION_DEPLOY" --commit "$commit" --branch "$branch"' in text
    assert 'exec "$STAGING_DEPLOY" --commit "$commit" --branch "$branch"' in text


def test_strict_gateway_rejects_extra_flags_and_untrusted_branches():
    cases = [
        "",
        "bash -se",
        "mkdir -p /opt/munshi/runtime/code-only",
        "scp -t /opt/munshi/runtime/",
        GOOD + " --bundle /tmp/data",
        GOOD + "; id",
        GOOD.replace(BRANCH, "main"),
        GOOD.replace(BRANCH, "feat/other-branch"),
        GOOD.replace(SHA, "invalid"),
    ]
    for case in cases:
        result = _gateway(case)
        assert result.returncode == 71, (case, result.stderr)
        assert "rejected" in result.stderr


def test_valid_narrow_gateway_verb_requires_installed_root_owned_wrapper():
    # CI/test environment does not install the privileged production wrapper.
    # Production installation is deliberately a separate admin-controlled step.
    text = GATEWAY.read_text()
    assert 'CODE_ONLY_DEPLOY="/opt/munshi/bin/deploy-code-only-release"' in text
    assert '[[ -x "$CODE_ONLY_DEPLOY" ]]' in text
    assert 'exec "$CODE_ONLY_DEPLOY" --commit "$commit" --branch "$branch"' in text
    assert 'CODE_ONLY_BASE="a9704e5861c6741c3df3fe7caf085410ddafaefa"' in text


def test_new_wrapper_uses_authenticated_bounded_stdin_not_arbitrary_scp():
    source = WRAPPER.read_text()
    assert 'timeout 120 dd bs=1048576 count=256 status=none of="$bundle"' in source
    assert 'git bundle verify "$bundle"' in source
    assert "Exact target bundle missing" not in source
    assert 'rollback() {' in source
    assert 'rm -f -- "$bundle"' in source
    assert "Unreviewed source change" in source
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in source


def test_deploy_workflow_disallows_arbitrary_remote_shell_upload():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/netcup-code-only-deploy.yml"
    ).read_text()
    assert 'DEPLOY_COMMAND="/opt/munshi/bin/deploy-code-only-release' in workflow
    assert 'cat "$RUNNER_TEMP/munshi-codeonly.bundle" |' in workflow
    assert '"${SSH[@]}" "$NETCUP_USER@$NETCUP_HOST" "$DEPLOY_COMMAND"' in workflow
    assert "scp " not in workflow
    assert "REMOTE_DIR=" not in workflow
