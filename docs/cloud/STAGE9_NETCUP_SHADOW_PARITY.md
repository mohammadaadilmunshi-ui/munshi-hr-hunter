# Stage 9 Netcup Shadow Parity

- Timestamp: 2026-08-31 (America/New_York)
- State: waiting for explicit host and SSH access
- Branch: `feat/cloud-migration-foundation`
- Architecture target: `x86_64`
- OS target: Ubuntu 24.04 LTS
- n8n target: `2.22.5`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Production Mac mutation count: `0`

`scripts/netcup/deploy_shadow.sh` checks out an exact feature-branch commit, verifies the canonical workflow digest, generates server-local synthetic Hunter and n8n secrets with mode 0600, renders a cloud workflow copy outside Git, builds/pulls the three-service stack, starts only shadow-safe lanes, and pulls `gemma3:4b`. It imports no workflow credentials and no production state.

`scripts/netcup/verify_shadow.sh` is the authoritative GO/NO-GO gate. It verifies host resources, Docker/Compose, amd64 images, restart policies, expected named mounts, loopback-only bindings, absent production credentials, FastAPI authenticated status, Streamlit, n8n 2.22.5, container DNS in all required directions, Ollama model/generation, real HR Agent adapter/proxy scoring, actual Chromium/Playwright launch, absence of Chromium zombies, disposable Hunter SQLite, n8n state, model persistence across restart, the exact cloud Git commit, renderer endpoint classification, and the canonical digest.

The validator records a machine-readable JSON report under `/opt/munshi/reports`. It returns only `GO_STAGE9_CLOUD_SHADOW` when every required check passes; otherwise it returns `NO_GO_STAGE9_CLOUD_SHADOW`. A passing initial validator is not the final Stage 9 proof until reboot and endurance milestones also pass.

Physical storage performance is a Stage 9 concern. `scripts/netcup/benchmark_host.sh` records block-device metadata and runs a bounded 512 MiB, 30-second `fio` probe after deployment. Results are interpreted as observed guest performance; `/dev/vda`, absence of a guest `nvme*` name, and `ROTA=1` do not contradict Netcup's 512 GB NVMe SSD provider contract under KVM.

Expected final safety fields remain:

```text
PRODUCTION_MAC: UNCHANGED
PRODUCTION_MAC_MUTATIONS: 0
PRODUCTION_STATE_MIGRATION: NOT_STARTED
TELEGRAM_CLOUD_PRODUCTION: DISABLED
DISCOVERY_CLOUD_PRODUCTION: DISABLED
CUTOVER: NOT_STARTED
```
