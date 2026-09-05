from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / "deploy" / "netcup" / "recover_production_hunter.sh"
INSTALLER = ROOT / "deploy" / "netcup" / "install_production_hunter_recovery_gateway.sh"
GATEWAY = ROOT / "deploy" / "netcup" / "github_deploy_gateway.sh"
EXPECTED_SHA = "380896964d12199936ee7c676e39352a1a68cec8"


def test_recovery_is_exact_sha_and_hunter_only() -> None:
    text = RECOVERY.read_text()
    assert f'EXPECTED_SHA="{EXPECTED_SHA}"' in text
    assert 'up -d --no-deps --force-recreate hunter' in text
    assert 'PRODUCTION_HUNTER_RECOVERY_HEALTH=PASS' in text
    assert 'DATABASE_RESTORED_OR_REPLACED=NO' in text
    assert 'N8N_RECREATED=NO' in text
    assert 'OLLAMA_RECREATED=NO' in text
    assert 'CADDY_RECREATED=NO' in text
    assert 'PRODUCTION_DB_BACKUP_QUICK_CHECK=PASS' in text
    assert 'PRODUCTION_DB_LIVE_QUICK_CHECK=PASS' in text

    forbidden = (
        "docker compose down",
        "docker rm $N",
        'docker rm "$N"',
        "docker rm $O",
        'docker rm "$O"',
        "docker restart $N",
        'docker restart "$N"',
        "docker restart $O",
        'docker restart "$O"',
        "docker stop $N",
        'docker stop "$N"',
        "docker stop $O",
        'docker stop "$O"',
    )
    for token in forbidden:
        assert token not in text


def test_gateway_exposes_only_exact_recovery_shape() -> None:
    text = GATEWAY.read_text()
    exact = f"/opt/munshi/bin/recover-production-hunter --expected-sha {EXPECTED_SHA}"
    assert exact in text
    assert 'PRODUCTION_HUNTER_RECOVERY="/opt/munshi/bin/recover-production-hunter"' in text
    assert "request rejected by MUNSHI GitHub deployment gateway" in text
    assert "recover-production-hunter\\ --expected-sha\\ ([0-9a-f]{40})" not in text


def test_installer_is_transactional_and_does_not_touch_runtime() -> None:
    text = INSTALLER.read_text()
    assert 'RESULT=PRODUCTION_HUNTER_RECOVERY_GATEWAY_INSTALLED' in text
    assert 'RESULT=PRODUCTION_RECOVERY_GATEWAY_INSTALL_ROLLED_BACK' in text
    assert 'AUTHORIZED_KEYS_CHANGED=NO' in text
    assert 'CONTAINERS_CHANGED=NO' in text
    assert 'DATABASE_CHANGED=NO' in text
    for token in ("docker stop", "docker restart", "docker compose", "docker rm"):
        assert token not in text
