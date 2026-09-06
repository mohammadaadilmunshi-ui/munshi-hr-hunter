from pathlib import Path


def test_production_vault_activation_is_hunter_only_and_secret_safe() -> None:
    root = Path(__file__).resolve().parent.parent
    script = (root / "deploy" / "netcup" / "configure_production_vault.sh").read_text(encoding="utf-8")

    assert "MUNSHI_VAULT_KEY" in script
    assert "os.urandom(32)" in script
    assert "urlsafe_b64encode" in script
    assert "EXISTING_ENCRYPTED_RECORD_COUNT" in script
    assert "Refusing to create a replacement key" in script
    assert "up -d --no-deps --force-recreate hunter" in script
    assert "PRODUCTION_N8N_RECREATED=NO" in script
    assert "PRODUCTION_OLLAMA_RECREATED=NO" in script
    assert "PRODUCTION_VAULT_AES_GCM_SELF_TEST=PASS" in script
    assert "verify-production-runtime-contract" in script

    # Every Python heredoc executed inside Hunter must attach stdin explicitly.
    # Without `docker exec -i`, `python -` receives EOF and a smoke/self-test can
    # silently do nothing while still returning exit code 0.
    assert 'docker exec -i "$H" python - <<\'PY\'' in script
    assert 'docker exec "$H" python - <<\'PY\'' not in script

    lowered = script.lower()
    assert "docker compose down" not in lowered
    assert "down -v" not in lowered
    assert "docker volume rm" not in lowered
    assert "echo $munshi_vault_key" not in lowered
    assert "echo ${munshi_vault_key" not in lowered


def test_profile_v2_never_auto_confirms_rebuild() -> None:
    root = Path(__file__).resolve().parent.parent
    runtime = (root / "app" / "profile_runtime_repair_v2.py").read_text(encoding="utf-8")
    assert "Rebuild preview from current Master Resume" in runtime
    assert "confirm_profile_extract" not in runtime
    assert "never confirms" in runtime
