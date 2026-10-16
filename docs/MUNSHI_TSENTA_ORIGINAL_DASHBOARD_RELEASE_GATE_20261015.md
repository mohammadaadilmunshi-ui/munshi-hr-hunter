# MUNSHI × Tsenta — Original Dashboard Release Gate
**Assessment:** October 15, 2026
**Target:** the existing https://dashboard.munshi.systems application, not a separate website.
**Repository:** mohammadaadilmunshi-ui/munshi-hr-hunter; stacked PR #104 on draft PR #103.

## Source-level functionality implemented

- Existing Dashboard includes Native/Tsenta engine health and secure connection panel.
- Existing job cards preserve Original Job, verified Apply Job, and Prepare Application.
- Applications workspace shows exact routed owner, safe attempts, status, and separate employer receipt.
- Authenticated backend selection API prevents Tsenta route creation without a verified execution backend.
- Tenant-bound OAuth PKCE, DCR or pre-registered client ID, encrypted token storage using existing AES-GCM server vault, refresh/replay/disconnect handling, one-time callback.
- Read-only MCP client with JSON, SSE, session initialization and a strict read-tool allowlist; verifies existing Tsenta account profile without retaining profile data.
- Existing provider-owned application status may be reconciled without persisting raw Tsenta ID; provider-reported submitted does not equal an employer receipt.
- Tenant-safe sanitized ATS field learning, provider state and regression fixtures.
- Shared authenticated status projection for extension/dashboard clients: GET /api/jobs/{job_id}/application-provider-status. It has no raw candidate data or submit authority.
- Additive migrations 058 (provider/observations) and 059 (OAuth tables). Synthetic local fixture proof and tests completed.

## Pre-staging hold points

1. Review PR #103 exact signed LinkedIn job destination and broad ATS board evidence before merging its stacked descendant PR #104.
2. Confirm CI green on exact head SHA, Linux/Docker/transport checks and a clean local worktree.
3. Identify the approved staging Hunter SHA/branch and Apply image; do not use production data as staging source.
4. Back up **staging** SQLite to an independent, encrypted, tested backup. Apply 058/059 in isolated staging and run PRAGMA integrity_check, schema replay and rollback; do not mutate production to test a migration.
5. Review /home/munshi/munshi-staging-edge/Caddyfile. The verified Oct 15 configuration has HTTPS proxy and auth gates, but no explicit Tsenta callback route. The OAuth callback GET /api/integrations/tsenta/oauth/callback must reach the correct staging FastAPI service without asking the external provider to supply the dashboard's basic-auth password. Only this tightly scoped endpoint may be exposed; all other admin/API endpoints remain protected. Invalid state must return a bounded non-sensitive error with no table creation or remote call.
6. Leave MUNSHI_TSENTA_OAUTH_CALLBACK_VERIFIED=false until the externally invoked staging callback is proven end to end, including redirect, provider DCR client registration, matching origin, and no sensitive code/state data in logs.
7. Connect Tsenta explicitly through a **new staging backend consent** (not Mac Codex) and prove token refresh, one-time state, profile-read availability, disconnect, and credentials encrypted in the tenant scope.
8. Read-only check of auth readiness must not imply application execution is ready. There is currently no verified hosted Tsenta dispatch worker; current backend gate deliberately returns execution_ready=false.
9. Prove current Apply/extension state versions and exact application/session identity match the Hunter shared read-only status API. Do not patch or reset existing dirty local Apply checkout. Use a clean worktree of its actual deployed source. Prior local manifest was 0.2.11 while installed extension was reported as 0.2.12.
10. Prove Native Prepare -> verified ATS URL -> correct tailored PDF -> form field mapping -> READY_FOR_REVIEW in independent staging, including Workday and at least one other ATS. Production incident Crescent R441 cannot be replayed based on historical approval.
11. Establish idempotent dispatch/receipt evidence before enabling any Tsenta or native live submit: exact route ownership, package digests, resume upload/checks, preflight current, work authorization truth, dedup and ambiguous submission handling. One real employer receipt is required before claiming actual deployment readiness for autonomous submit.

## Production deployment (only after the gates pass)

- Exact SHA, manual GitHub Actions promotion and reviewed rollback. No auto-deploy from push or PR.
- Preserve all five Hunter Netcup Compose layers including stage12-n8n-runtime-repair.override.yaml, the read-only n8n volume mount, and existing internal Ollama.
- Production currently had Hunter b8cd45ea0; staging Hunter b8b6234a in the Oct 15 audit. Treat them as historical snapshots; re-check.
- Promote Hunter only through the established restricted Git bundle + forced-command SSH gateway. Apply/extension use their independent release contracts.
- Compare n8n/Ollama container identity before/after, verify health and DB integrity, and roll back automatically if validation fails.
- User confirmation remains required for live account authorization and an approved real application attempt.

## Verification meaning

**"Source tests passed"** does not mean Netcup OAuth is authorized, a resume was uploaded to an employer, Workday completed, the extension installed, or an employer receipt was confirmed. The release is not finished until those separate real-world checks are evidenced.

The code deliberately declines an unauthenticated / duplicate / unsupported live route and never fabricates a "Submitted" result.
