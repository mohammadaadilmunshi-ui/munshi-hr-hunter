# Staging infrastructure discovery

Discovery timestamp: 2026-09-04 UTC. This was read-only; no staging or production mutation occurred.

## GitHub and source contracts

- **VERIFIED:** The local source contains manual-only [Netcup Staging Deploy](../../.github/workflows/netcup-staging-deploy.yml) and paired staging deploy/verification scripts. The workflow requires an exact SHA, authenticated Git bundle transport, `environment: staging`, and the `munshi-netcup-staging` concurrency group.
- **VERIFIED:** Repository safety checks require the staging project path `/home/munshi/munshi-staging-v1`, staging side-effect guards, isolated volumes, loopback raw ports, dashboard configuration, and no n8n/Ollama/edge recreation.
- **VERIFIED:** The intended server wrapper is `/opt/munshi/bin/deploy-staging-release --commit <40-char-sha> --branch <branch>`; it is invoked only from the manual GitHub workflow.
- **VERIFIED:** GitHub authentication is valid and the repository is readable. The candidate branch is `feat/autonomous-career-os-foundation`; its remote state must still match the exact local release SHA immediately before deployment.
- **VERIFIED:** The staging branch is supplied as the manual workflow `source_branch` input. The live staging checkout is currently clean on `feat/product-ui-v2` at `2687480625e2daa4210cf49f3dab28899e8f0043`; it is intentionally different from the Career OS candidate branch pending canonical staging deployment.

## Netcup runtime

- **VERIFIED:** Read-only BatchMode SSH to `munshi@159.195.244.16` succeeded. The staging root is `/home/munshi/munshi-staging-v1`, with repository `/home/munshi/munshi-staging-v1/repo`, environment file `staging.env`, and `staging.override.yaml`.
- **VERIFIED:** Current staging containers are `munshi-netcup-staging-hunter-1`, `munshi-netcup-staging-n8n-1`, `munshi-netcup-staging-ollama-1`, and edge `munshi-staging-edge-caddy`. All are running, healthy, OOM-free, and have restart count zero.
- **VERIFIED:** The rendered staging stack uses `compose.yaml`, `compose.netcup-shadow.yaml`, and `/home/munshi/munshi-staging-v1/staging.override.yaml`. Its database path inside Hunter is `/app/hunter/data/hunter.db`; `PRAGMA quick_check` is `ok`.
- **VERIFIED:** Staging volumes are exclusively prefixed `munshi-netcup-staging_`. Production uses separate `munshi-netcup-shadow_*` volumes; its Hunter read-only n8n mount remains distinct. This positively proves database/volume separation.
- **VERIFIED:** Staging raw ports are loopback-only: Hunter `127.0.0.1:18000`, Streamlit `127.0.0.1:18501`, and n8n `127.0.0.1:15678`.
- **VERIFIED:** Caddy routes `staging-dashboard.munshi.systems` only to staging Streamlit at `127.0.0.1:18501`; the protected production domain routes separately to `127.0.0.1:8501`. Both return HTTP 401 when unauthenticated.
- **VERIFIED:** `/opt/munshi/bin/deploy-staging-release` is installed and executable. It runs staging and production non-regression verification, rejects dirty staging source, creates and validates a WAL-safe SQLite backup under `/home/munshi/munshi-staging-v1/backups`, validates authenticated bundle ancestry, builds/recreates Hunter only, verifies n8n/Ollama/edge identity and start times, verifies production container identity, and automatically rolls Hunter back on failure.
- **VERIFIED:** The stable staging verifier passed volume isolation, side-effect flags/process absence, loopback ports, local HTTP health, DB integrity, dashboard configuration, and HTTPS authentication-boundary checks. Free disk space was 371G of 503G.

## Decision

Staging mutation is authorized only after the reviewed local release candidate is committed, pushed, and its exact SHA is confirmed on the candidate branch. Runtime isolation, rollback, and health prerequisites are now positively proven. Production remains out of scope.

## Local staging-fixture source contract

- **VERIFIED:** `migrations/023_staging_synthetic_fixtures.py` and `app/staging_fixtures.py` add an isolated, additive fixture ledger. Synthetic jobs are owned by that ledger and have `environment=staging`, `synthetic=true`, `is_test_data=true`, `source=staging_fixture`, and `external_actions_disabled=true` metadata.
- **VERIFIED:** The seed tool defaults to dry-run, requires exact positive `staging` identity, rejects production-like database paths, and may delete only fixture-ledger job IDs. Fixture employers are fictional `TEST/STAGING` names and all URLs use the reserved `.invalid` domain.
- **VERIFIED:** `canonical_jobs_v1` excludes only fixture-ledger IDs for canonical metrics; actual staging dashboard metric consumers still require live-staging validation against this contract before fixtures are exposed.
- **INFERRED:** The host staging database can use this mechanism only after its actual isolated database path and staging identity are verified read-only. Local source validation is not host/runtime proof.
