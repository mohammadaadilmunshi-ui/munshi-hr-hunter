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
    assert "7d6861a88cc2eb8139632e3c54573ff9909bfd2f" in content
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
            "--expected-base", "7d6861a88cc2eb8139632e3c54573ff9909bfd2f",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Exact target SHA required" in result.stderr

def test_actual_reviewed_source_delta_is_strictly_whitelisted():
    # Validate exact reviewed root-candidate rather than installed older script.
    import re
    from tests.test_free_discovery_gateway_handoff import proposal

    handoff = proposal()
    assert handoff["BASE_SHA"] == "504ea4ddcd9a624357ba5139629ac79c7dcefda1"
    original = SCRIPT.read_text()
    script = handoff["transform"]("deploy-code-only-release", original)
    def allowed(script):
        match = re.search(r"allowed_files=\(\n(?P<paths>.*?)\n\)", script, re.DOTALL)
        assert match is not None
        return set(match.group("paths").split())
    candidate_paths = allowed(script)
    original_paths = allowed(original)
    diff = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only",
         handoff["BASE_SHA"], "HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    changed = set(diff.stdout.splitlines())
    assert changed and changed <= candidate_paths
    assert changed - original_paths <= set(handoff["ALLOWLIST"])
    assert not any("*" in path or path.startswith("migrations/") for path in candidate_paths)
    assert not any(path in ("Dockerfile", "compose.yaml", "compose.netcup-shadow.yaml")
                   for path in changed)
    assert all((not name.startswith("scripts/"))
               or name in {"scripts/free_discovery_audit.py", "scripts/free_discovery_canary.py"}
               for name in changed)
    assert "Unreviewed source change" in script
    assert "Code-only release forbids file deletion or renaming" in script


def test_free_discovery_workflow_expected_live_baseline():
    workflow = WORKFLOW.read_text()
    assert '[[ "$EXPECTED_LIVE" == "504ea4ddcd9a624357ba5139629ac79c7dcefda1" ]] || exit 3' in workflow
    assert "cancel-in-progress: false" in workflow
    assert "environment: production" in workflow


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
    cli = (ROOT / "app/linkedin_link_recovery_v7.py").read_text()
    assert "?mode=ro" in cli
    assert "PRAGMA query_only=ON" in cli
    assert "persist_verified_evidence" not in cli
    script = SCRIPT.read_text()
    for name in ("test_linkedin_apply_button_v7.py", "test_linkedin_link_recovery_v7.py",
                 "test_linkedin_verified_url_evidence_v5.py", "test_job_destination_v1.py"):
        assert f"/app/hunter/tests/{name}" in script


def test_24h_auth_protected_image_tests_and_allowlist():
    content = SCRIPT.read_text()
    for path in (
        "app/device_auth.py", "deploy/netcup/dashboard_device_cookie_caddy_v1.py",
        "tests/test_device_auth.py", "tests/test_dashboard_device_cookie_caddy_v1.py",
    ):
        assert "  " + path + "\n" in content
    assert "/app/hunter/tests/test_device_auth.py" in content


def test_aggressive_resume_certification_and_receipt_release_scope():
    content = SCRIPT.read_text()
    for name in (
        "app/native_resume_service_v5.py", "app/prepare_worker_adapters_v1a.py",
        "app/resume_one_page_guard_v1.py", "app/application_review_card_v1.py",
        "tests/test_native_resume_cert_retention_v1.py",
        "tests/test_application_review_card_v1.py",
        "tests/test_prepare_worker_adapters_v1a.py",
    ):
        assert "  "+name+"\n" in content
    assert "/app/hunter/tests/test_native_resume_cert_retention_v1.py" in content

def test_lossless_certificates_stage_b_relevance_and_receipt_poll_are_in_release():
    content=SCRIPT.read_text()
    for item in (
        "app/master_certifications_v1.py",
        "app/deterministic_profile_extractor_v1.py",
        "app/jd_requirement_match_v1.py",
        "app/relay_receipt_worker_v1.py",
        "docker/hunter-supervisor.py",
        "tests/test_master_certifications_v1.py",
        "tests/test_jd_match_evidence_v11.py",
        "tests/test_relay_receipt_worker_v1.py",
        "tests/test_certificate_rendered_pdf_v1.py",
        "app/resume_studio_page_v2.py",
        "app/resume_studio_page_v4.py",
        "app/resume_engine_selector.py",
        "tests/test_aggressive_default_across_surfaces_v1.py",
    ):
        assert "  "+item+"\n" in content
    assert "/app/hunter/tests/test_certificate_rendered_pdf_v1.py" in content


def test_v11_jd_and_ats_performance_protection_are_released():
    contents=SCRIPT.read_text()
    for src in (
        "app/jd_intelligence_v1.py",
        "app/prepare_worker_adapters_v1c.py",
        "tests/test_jd_intelligence_quality_v11.py",
        "tests/test_aggressive_non_submitting_e2e_v11.py",
        "tests/test_prepare_worker_adapters_v1c.py",
    ):
        assert "  "+src+"\n" in contents
    assert "/app/hunter/tests/test_aggressive_non_submitting_e2e_v11.py" in contents

def test_cross_platform_application_reliability_release_is_exactly_allowlisted():
    content=SCRIPT.read_text()
    for item in (
        "app/application_issue_intelligence_v1.py",
        "app/application_recovery_v1.py",
        "app/application_view_model_v1.py",
        "app/unified_job_workspaces_v1.py",
        "app/product_experience_vnext.py",
        "app/prepare_worker_adapters_v1c.py",
        "tests/test_application_issue_intelligence_v1.py",
        "tests/test_application_recovery_v1.py",
        "tests/test_product_experience_vnext.py",
        "tests/test_unified_job_workspaces_v1.py",
    ):
        assert "  "+item+"\n" in content
    for item in (
        "tests/test_application_issue_intelligence_v1.py",
        "tests/test_application_recovery_v1.py",
        "tests/test_product_experience_vnext.py",
        "tests/test_unified_job_workspaces_v1.py",
    ):
        assert "/app/hunter/"+item in content


def test_relay_verified_mail_outbox_is_in_signed_code_only_bundle():
    content=SCRIPT.read_text()
    for item in (
        "app/application_notifications_runtime_v3.py",
        "app/relay_inbox_receipt_v1.py",
        "app/relay_receipt_worker_v1.py",
        "tests/test_relay_provider_notifications_v1.py",
    ):
        assert "  "+item+"\n" in content
    assert "/app/hunter/tests/test_relay_provider_notifications_v1.py" in content


def test_smtp_code_only_release_scope_is_explicit_and_tested():
    source = SCRIPT.read_text()
    for path in ("app/integration_credentials_ui_v1.py",
                 "tests/test_smtp_sender_validation_v1.py"):
        assert f"  {path}\n" in source
    assert "/app/hunter/tests/test_smtp_sender_validation_v1.py" in source
    assert ".github/workflows/netcup-code-only-deploy.yml" in source



def test_account_and_signed_import_release_files_are_explicitly_allowed():
    source = SCRIPT.read_text()
    for item in (
        "app/integration_credentials_v1.py",
        "app/linkedin_cross_source_probe_v1.py",
        "app/linkedin_signed_evidence_import_v1.py",
        "tests/test_linkedin_cross_source_probe_v1.py",
        "tests/test_linkedin_signed_evidence_import_v1.py",
    ):
        assert ("  " + item + chr(10)) in source
    assert "/app/hunter/tests/test_linkedin_signed_evidence_import_v1.py" in source
    assert "/app/hunter/tests/test_ats_managed_identity.py" in source
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in source



def test_branded_notification_release_files_scoped_and_smoked():
    source = SCRIPT.read_text()
    for path in ("app/application_notifications_v1.py",
                 "app/notification_email_design_v1.py",
                 "tests/test_notification_email_design_v1.py"):
        assert ("  " + path + chr(10)) in source
    assert "/app/hunter/tests/test_notification_email_design_v1.py" in source
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in source



def test_source_label_release_changes_are_scoped():
    script = SCRIPT.read_text()
    for name in (
        "app/source_provenance.py",
        "app/application_runs_v1.py",
        "tests/test_source_label_presentation_v1.py",
        "app/job_destination_v1.py",
        "app/notification_email_design_v1.py",
    ):
        assert ("  " + name + chr(10)) in script
    assert "/app/hunter/tests/test_source_label_presentation_v1.py" in script
    assert "CODE_ONLY_NEW_DATABASE_BACKUP=NONE" in script
