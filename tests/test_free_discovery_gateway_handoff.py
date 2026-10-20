"""Check the proposed root handoff without executing any installer action."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'deploy/review/install_free_discovery_gateway_20261020.sh'


def proposal():
    python = INSTALLER.read_text().split("<<'PY_ROOT_INSTALLER'\n", 1)[1].rsplit('PY_ROOT_INSTALLER', 1)[0]
    parsed = ast.parse(python)
    constants = {'BASE_SHA', 'OLD_BASE', 'ALLOWLIST', 'EXTRA_TESTS', 'EXPECTED', 'NEXT'}
    allowed = [n for n in parsed.body if isinstance(n, (ast.Import, ast.ImportFrom))
               or isinstance(n, ast.FunctionDef) and n.name == 'transform'
               or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets)]
    namespace = {}
    exec(compile(ast.Module(body=allowed, type_ignores=[]), str(INSTALLER), 'exec'), namespace)
    return namespace


def test_protected_scripts_stay_at_proven_installed_checksums():
    p = proposal()
    for name, source in {
        'deploy-code-only-release': ROOT / 'deploy/netcup/deploy_code_only_release.sh',
        'github-deploy-gateway': ROOT / 'deploy/netcup/github_deploy_gateway.sh',
    }.items():
        original = source.read_text()
        assert hashlib.sha256(original.encode()).hexdigest() == p['EXPECTED'][name]
        transformed = p['transform'](name, original)
        assert hashlib.sha256(transformed.encode()).hexdigest() == p['NEXT'][name]
        assert subprocess.run(['bash', '-n'], input=transformed, text=True).returncode == 0


def test_proposed_allowlist_is_explicit_and_does_not_drop_safety_checks():
    p = proposal()
    old = (ROOT / 'deploy/netcup/deploy_code_only_release.sh').read_text()
    new = p['transform']('deploy-code-only-release', old)
    block = lambda s: re.search(r'allowed_files=\(\n(.*?)\n\)', s, re.S).group(1)
    old_paths = set(block(old).split())
    new_paths = set(block(new).split())
    assert old_paths <= new_paths
    assert new_paths - old_paths == set(p['ALLOWLIST']) - old_paths
    assert not any('*' in path or path.startswith('migrations/') for path in p['ALLOWLIST'])
    for token in (
        'stage10-imported.override.yaml', 'stage12-production.override.yaml',
        'stage12-n8n-runtime-repair.override.yaml', 'compose.netcup-shadow.yaml', 'compose.yaml',
        'git merge-base --is-ancestor', 'git checkout -q -B',
        'CODE_ONLY_SCHEMA_MIGRATIONS=NONE', '--network none --read-only',
        'db_schema', 'old_mount', 'rollback_image',
    ):
        assert token in old and token in new
    assert p['BASE_SHA'] == '504ea4ddcd9a624357ba5139629ac79c7dcefda1'


def test_installer_is_default_check_only_and_does_not_deploy():
    shell = INSTALLER.read_text()
    assert 'mode="${1:---check}"' in shell
    assert 'os.geteuid() != 0' in shell
    assert 'EXPECTED[name]' in shell and 'NEXT[name]' in shell
    assert 'reversed(installed)' in shell
    assert 'WORKFLOW_REPIN_REVIEW_REQUIRED' in shell
    assert 'sudo ' not in shell
    assert subprocess.run(['bash', '-n', str(INSTALLER)]).returncode == 0
