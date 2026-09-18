# Stage 8B Waiting for Netcup Provisioning

- Timestamp: 2026-08-31 23:34 America/New_York (2026-09-01 03:34 UTC)
- Prepared source HEAD: `36de37fedb8348385b631c64c1baa10f3ed005b9`
- Branch: `feat/cloud-migration-foundation`
- Main: `374d9ae2a9a0cb8fa85825803a9c2f25205b8866` (unchanged locally and on origin)
- Host: `WAITING_FOR_NETCUP_PROVISIONING`
- Architecture/OS target: Ubuntu 24.04 LTS x86_64
- Docker/Compose versions: pending host; merged Compose validated locally and image build passed in GitHub x86_64 CI
- n8n target: `2.22.5`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Git status at handoff generation: clean and synchronized with origin
- Safety status: `PASS`
- Production Mac mutation count: `0`

## Completed

- Repository identity, branch, remote, Stage 6B ancestry, main immutability, clean baseline, and architecture were forensically verified.
- The canonical workflow remained byte-identical. No canonical workflow edit or cloud import occurred.
- The Netcup shadow environment contract and placeholder-only example were created and cross-validated against real source controls.
- The additive amd64 Compose layer adds restart policies, healthchecks, bounded logs, project-scoped named persistence, Docker DNS, loopback-only administrative ports, and explicit disabled production lanes.
- Fresh-volume database bootstrap was repaired to create required schedule, source-runtime truth, Telegram delivery, n8n queue, and progress relations. Clean synthetic tests now render every dashboard page.
- Idempotent scripts were prepared for host bootstrap, shadow deploy, authoritative verification, benchmark, reboot proof, endurance watch/report, failure classification, local validation, cleanup, and top-level orchestration.
- Security, SSH, backup, state migration, controlled cutover, parity, endurance, environment, baseline, and operator documents were created.
- A dedicated Ed25519 key was created only because the exact files were absent. Its public fingerprint is `SHA256:ZAsn333cd2gYOrfrAb1G2cqsxDC8mdC2xdLPF/1nCmc`. SSH config was not edited and key material is not in Git.
- Local result: 157 tests passed; Python compilation, five source/portability validators, shell syntax, renderer classification, merged Compose config, canonical digest, and tracked-file hygiene passed.
- The Mac Docker daemon was stopped. It was not started because that would change Mac process/Docker state; therefore the disposable local container start was not attempted. GitHub's Docker Foundation job built and inspected the Hunter image successfully on x86_64.
- CI for prepared source HEAD: Linux Compatibility `PASS` (43s), Docker Foundation `PASS` (2m1s). Draft PR #1 remains open and unmerged.

## Prepared scripts

- `scripts/netcup/run_stage8b_stage9.sh`
- `scripts/netcup/bootstrap_netcup_host.sh`
- `scripts/netcup/deploy_shadow.sh`
- `scripts/netcup/verify_shadow.sh`
- `scripts/netcup/benchmark_host.sh`
- `scripts/netcup/reboot_proof.sh`
- `scripts/netcup/endurance_watch.sh`
- `scripts/netcup/endurance_report.sh`
- `scripts/netcup/classify_failure.sh`
- `scripts/netcup/local_preapproval_validate.sh`

Runtime reports will be written under `/opt/munshi/reports` and `/opt/munshi/reports/netcup`; no report contains secret values.

## Required inputs

1. An explicit provisioned Netcup host/IP.
2. Ubuntu 24.04 LTS x86_64 on that host.
3. Initial root SSH access that accepts the dedicated identity, or a safe human installation of its public key before running the operator.
4. Netcup console recovery access retained by the human account owner.

No IP, datacenter, credentials, email, billing detail, or server state was guessed or scraped.

## Exact resume command

```bash
NETCUP_HOST=<HOST> scripts/netcup/run_stage8b_stage9.sh \
  --host <HOST> \
  --identity "$HOME/.ssh/munshi_netcup_ed25519" \
  --bootstrap --deploy --verify --benchmark --reboot-proof \
  --endurance-hours 1 --report
```

The bootstrap performs a read-only hardware/OS gate before remote mutation. Any material mismatch returns `NO_GO_NETCUP_HARDWARE_MISMATCH`. Stage 9 success still stops before production state migration, Telegram/discovery authority, DNS, merge, or cutover.

```text
RESULT: WAITING_FOR_NETCUP_PROVISIONING
PRODUCTION_MAC: UNCHANGED
PRODUCTION_MAC_MUTATIONS: 0
PRODUCTION_STATE_MIGRATION: NOT_STARTED
TELEGRAM_CLOUD_PRODUCTION: DISABLED
DISCOVERY_CLOUD_PRODUCTION: DISABLED
CUTOVER: NOT_STARTED
```
