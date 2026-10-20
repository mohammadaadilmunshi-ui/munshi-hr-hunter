#!/usr/bin/env bash
# Owner-only reviewed installer. Default is read-only checksum verification.
# Never invokes sudo, Docker, a workflow, deployment, or a database operation.
set -Eeuo pipefail
mode="${1:---check}"
python3 - "$mode" <<'PY_ROOT_INSTALLER'
# Prepared for owner review only. Does not deploy or alter service/database state.
import hashlib, json, os, re, stat, subprocess, sys, tempfile
from pathlib import Path
BASE_SHA = '504ea4ddcd9a624357ba5139629ac79c7dcefda1'
OLD_BASE = '7d6861a88cc2eb8139632e3c54573ff9909bfd2f'
ALLOWLIST = ['.github/workflows/docker-foundation.yml',
 '.github/workflows/free-discovery-regression.yml',
 'app/application_runs_v1.py',
 'app/dashboard_adapter_sources_v2_3_1.py',
 'app/dashboard_targeting_gate.py',
 'app/database.py',
 'app/discovery_policy.py',
 'app/free_adapter_policy_v1_1.py',
 'app/google_jobs_jobspy_worker.py',
 'app/google_jobs_provider.py',
 'app/google_jobs_worker.py',
 'app/job_destination_v1.py',
 'app/jobspy_board_common.py',
 'app/market_source_common.py',
 'app/provider_adapter_common.py',
 'app/public_board_contracts.py',
 'app/public_board_http.py',
 'app/public_jobposting.py',
 'app/randomized_source_runner.py',
 'app/source_cooldown.py',
 'app/source_provenance.py',
 'app/source_readiness.py',
 'app/source_run_notifier.py',
 'app/source_runtime_state_v1.py',
 'app/sources/ashby.py',
 'app/sources/bamboohr.py',
 'app/sources/teamtailor.py',
 'app/sources/workday.py',
 'app/teamtailor_worker.py',
 'config/adapter_catalog.json',
 'config/adapter_source_mapping.json',
 'config/jobspy_boards.json',
 'config/public_board_contracts.json',
 'config/source_worker_registry.json',
 'deploy/review/install_free_discovery_gateway_20261020.sh',
 'docs/FREE_ATS_DISCOVERY_RECOVERY_20261020.md',
 'docs/free_discovery_canary_20261020.json',
 'docs/free_discovery_gateway_candidate_checksums_20261020.json',
 'docs/free_discovery_production_before_20261020.json',
 'scripts/free_discovery_audit.py',
 'scripts/free_discovery_canary.py',
 'tests/fixtures/free_discovery_failure_cohort_20261020.json',
 'tests/test_adapters.py',
 'tests/test_config_and_persistence.py',
 'tests/test_free_discovery_gateway_handoff.py',
 'tests/test_free_discovery_policy.py',
 'tests/test_public_board_discovery.py',
 'tests/test_randomized_source_runner.py',
 'tests/test_source_label_presentation_v1.py']
EXTRA_TESTS = ['test_free_discovery_policy.py',
 'test_public_board_discovery.py',
 'test_randomized_source_runner.py',
 'test_adapters.py',
 'test_adapter_pipeline_contract.py',
 'test_targeting.py',
 'test_config_and_persistence.py']
EXPECTED = {'deploy-code-only-release': 'c41625cd2d78d7a08c03c0e1c646b138adfaa02b35440b9f56f244859ed8e81a',
 'github-deploy-gateway': 'eea3d70dde5f7c89512b353319f4794b49f0e976d3b6975361707edff485290d'}
NEXT = {'deploy-code-only-release': 'c4c9fc168055c1a2dd89e0801db9168b9fad23be4584f80587375f5199b86b7a',
 'github-deploy-gateway': 'd93ac72f3029ba96c186d489e00f2fa22adc82d41411ee936b487a2bc87743c5'}

def transform(name, source):
    if name == "github-deploy-gateway":
        old = 'CODE_ONLY_BASE="' + OLD_BASE + '"'
        assert source.count(old) == 1
        return source.replace(old, 'CODE_ONLY_BASE="' + BASE_SHA + '"')
    assert name == "deploy-code-only-release"
    assert source.count("LIVE_BASE=" + OLD_BASE) == 1
    updated = source.replace("LIVE_BASE=" + OLD_BASE, "LIVE_BASE=" + BASE_SHA)
    match = re.search(r"allowed_files=\(\n(.*?)\n\)", updated, re.S)
    assert match
    old_paths = {line.strip() for line in match[1].splitlines() if line.strip()}
    replacement = "allowed_files=(\n" + "".join("  " + p + "\n" for p in sorted(old_paths | set(ALLOWLIST))) + ")"
    updated = updated[:match.start()] + replacement + updated[match.end():]
    anchor = "    /app/hunter/tests/test_job_destination_v1.py\necho 'EXACT_IMAGE_TESTS=PASS'"
    assert updated.count(anchor) == 1
    tests = "    /app/hunter/tests/test_job_destination_v1.py \\\n" + " \\\n".join("    /app/hunter/tests/" + name for name in EXTRA_TESTS)
    return updated.replace(anchor, tests + "\necho 'EXACT_IMAGE_TESTS=PASS'")

mode = sys.argv[1]
if mode not in ("--check", "--install"):
    raise SystemExit("Use --check or --install; owner must review before installation")
root = Path("/opt/munshi")
repo = root / "repo"
def git(*args):
    return subprocess.check_output(["git", "-c", "safe.directory=/opt/munshi/repo", "-c", "core.fsmonitor=false", "-C", str(repo), *args], text=True).strip()
if git("rev-parse", "HEAD") != BASE_SHA or git("status", "--porcelain"):
    raise SystemExit("Live source changed or dirty; stop and review")
if git("branch", "--show-current") != "feat/tsenta-connector-completion-20261015":
    raise SystemExit("Approved attached source branch required")
bin_info = (root / "bin").lstat()
if not stat.S_ISDIR(bin_info.st_mode) or bin_info.st_uid != 0 or bin_info.st_mode & 0o022:
    raise SystemExit("Root-controlled gateway directory required")
prepared = {}
originals = {}
for name in EXPECTED:
    path = root / "bin" / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit("Root-owned regular non-writable gateway required: " + name)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED[name]:
        raise SystemExit("Installed gateway checksum changed; stop: " + name)
    candidate = transform(name, raw.decode()).encode()
    if hashlib.sha256(candidate).hexdigest() != NEXT[name]:
        raise SystemExit("Candidate checksum mismatch: " + name)
    subprocess.run(["bash", "-n"], input=candidate, check=True)
    prepared[name] = candidate
    originals[name] = raw
print(json.dumps({"live_base": BASE_SHA, "candidate_sha256": NEXT, "mode": mode}, indent=2))
if mode == "--check":
    raise SystemExit(0)
if os.geteuid() != 0:
    raise SystemExit("Root-only installation; no escalation is attempted")
def replace_root_script(path, raw):
    fd, temporary = tempfile.mkstemp(prefix=".free-discovery-gateway-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
# Establish checksummed, root-owned rollback files before touching either gate.
for name, raw in originals.items():
    backup = root / "runtime" / (name + ".before-free-discovery-" + EXPECTED[name] + ".sh")
    if backup.exists():
        if backup.is_symlink() or hashlib.sha256(backup.read_bytes()).hexdigest() != EXPECTED[name]:
            raise SystemExit("Gateway rollback evidence mismatch")
    else:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
installed = []
try:
    for name, raw in prepared.items():
        replace_root_script(root / "bin" / name, raw)
        installed.append(name)
except BaseException:
    for name in reversed(installed):
        replace_root_script(root / "bin" / name, originals[name])
    raise
print("ROOT_GATEWAY_REPIN=INSTALLED; NO_DEPLOYMENT_PERFORMED; WORKFLOW_REPIN_REVIEW_REQUIRED")
PY_ROOT_INSTALLER
