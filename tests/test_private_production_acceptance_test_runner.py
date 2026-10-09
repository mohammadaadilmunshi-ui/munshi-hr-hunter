"""Ensure production regression tests can read owner-only source, in isolation."""
from pathlib import Path


def test_private_production_contract_runner_is_isolated_and_readable():
    root = Path(__file__).resolve().parents[1]
    source = (root / "deploy/netcup/deploy_production_release.sh").read_text()
    assert '=== RUN EXACT-IMAGE HUNTER AUTOAPPLY CONTRACT SUITE (NETWORK DISABLED) ===' in source
    fragment = source.split(
        '=== RUN EXACT-IMAGE HUNTER AUTOAPPLY CONTRACT SUITE (NETWORK DISABLED) ===',
        1,
    )[1].split('HUNTER_PRODUCTION_EXACT_IMAGE_AUTOAPPLY_TESTS=PASS', 1)[0]
    assert '--network none' in fragment
    assert '--read-only' in fragment
    assert '--user 0:0' in fragment
    assert '--mount "type=bind,src=$REPO/tests,dst=/app/hunter/tests,readonly"' in fragment
    assert "--entrypoint python" in fragment
    assert '-m pytest -q' in fragment
    assert 'netcup-shadow.env' not in fragment
    assert '/opt/munshi/secrets' not in fragment
    assert 'docker.sock' not in fragment
    assert 'type=volume' not in fragment
