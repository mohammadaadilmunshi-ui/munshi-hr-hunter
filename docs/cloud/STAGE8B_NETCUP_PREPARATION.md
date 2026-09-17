# Stage 8B Netcup Preparation

- Timestamp: 2026-08-31 (America/New_York)
- Preparation baseline HEAD: `274feb4dcd1cdf780e49cf8c1b8aede168c6b584`
- Branch: `feat/cloud-migration-foundation`
- Host: `WAITING_FOR_NETCUP_PROVISIONING`
- Architecture target: `x86_64`
- OS target: Ubuntu 24.04 LTS
- Docker/Compose/n8n runtime: verified by scripts when a host exists; n8n target `2.22.5`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Safety status: shadow-only
- Production Mac mutation count: `0`

Stage 8B adds an environment contract, additive Netcup Compose layer, host bootstrap, deployment, authoritative shadow verification, benchmark, reboot proof, endurance watcher/report, and a top-level operator. Local validation compiles source, runs all repository validators and tests, checks shell syntax, renders a disposable workflow copy, validates merged Compose, and starts a uniquely named synthetic Docker project with dynamic host ports.

The base topology remains Hunter, n8n 2.22.5, and Ollama. Cloud-specific behavior is additive. Named volumes remain project-scoped and store Hunter data/runtime/logs, n8n state, and Ollama models. The Netcup layer adds `linux/amd64`, `unless-stopped`, bounded JSON logs, Ollama health, and explicit shadow flags.

No host is guessed. If `NETCUP_HOST` is absent, the operator performs local validation and returns `RESULT: WAITING_FOR_NETCUP_PROVISIONING` with the exact resume command.

## Operator

Preparation only:

```bash
scripts/netcup/run_stage8b_stage9.sh --prepare-only
```

First full cloud milestone after provisioning and confirmed initial root SSH access:

```bash
NETCUP_HOST=<HOST> scripts/netcup/run_stage8b_stage9.sh \
  --host <HOST> \
  --identity "$HOME/.ssh/munshi_netcup_ed25519" \
  --bootstrap --deploy --verify --benchmark --reboot-proof \
  --endurance-hours 1 --report
```

Longer endurance milestones use `--endurance-hours 6`, then `24`, `48`, and `72`. A runtime-changing patch restarts the endurance clock. `--cleanup-shadow` stops only the project containers and intentionally retains named volumes.

## Hard boundaries

No production state, credentials, Telegram authority, discovery authority, callbacks, scheduler, coordinator, DNS, TLS, billing, merge, or cutover is enabled. Mac production remains authoritative and unchanged.
