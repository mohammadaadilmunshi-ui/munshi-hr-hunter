# STAGING_RELEASE_1 readiness

Prepared 2026-09-04 UTC. This is a release-candidate evidence package, not a deployment authorization. Production is excluded.

## VERIFIED_LOCAL

- Phase 0–8 foundations are a **staging candidate**, not production-ready. Scope: additive tenant ownership (015), Candidate Digital Twin (016), artifact/master-resume semantics (017), internal resume shadow (018), Answer Brain/sensitive vault (019), career policy (020), relationship evidence ledger (021), and preparation-only readiness ledger (022).
- Migration 023 adds only `staging_synthetic_jobs`, an ownership/metadata ledger for test fixtures. It does not alter canonical job semantics or action services.
- `app/staging_fixtures.py` provides deterministic fictional `TEST/STAGING` employers. Each fixture has durable equivalent metadata: `environment=staging`, `synthetic=true`, `is_test_data=true`, `source=staging_fixture`, and `external_actions_disabled=true`.
- `canonical_jobs_v1` is the explicit source-contract view for canonical metrics; it excludes only fixture-ledger job IDs. Existing live dashboard metric queries must be checked against that view during staging QA before synthetic fixtures are shown.
- The catalog covers HR Analyst, People Analytics, Talent Acquisition, HRIS, Compensation, HRBP, target rejections, work modes, salary/sponsorship states, Greenhouse/Lever/Ashby/Workday-like preparation, required-question/expected-salary/self-ID scenarios, duplicate and malformed/long descriptions, weak evidence, and prepared-artifact present/missing states.
- Seed default (safe dry-run):

  ```bash
  MUNSHI_STAGING_IDENTITY=staging .venv/bin/python tools/seed_staging_fixtures.py --database /path/to/staging/hunter.db
  ```

  An intentional local write additionally needs `--apply`; the tool rejects any identity other than exact `staging` and production-like database paths. Re-seeding deletes only job IDs listed in its own fixture ledger, then recreates the deterministic catalog. It never creates events, n8n receipts, application records, or outbound work.
- Local disposable SQLite validation applies migrations 015–023 in order from the base schema, verifies tenant/application-preparation tables, and seeds isolated fixtures. This does not prove the Netcup runtime migration path.

## VERIFIED_SOURCE_CONTRACT

- Expected manual deployment wrapper: `/opt/munshi/bin/deploy-staging-release --commit <40-char-sha> --branch <branch>`.
- Expected staging path from source safety guards: `/home/munshi/munshi-staging-v1`.
- The manual GitHub workflow is the expected authenticated bundle transport. It must retain isolated volumes, loopback raw ports, and no recreation of n8n, Ollama, or the edge.
- n8n remains authoritative. Native preparation is disabled by default and **Prepare != Submitted**. No real application may be submitted from staging.

## BLOCKED_EXTERNAL

- GitHub read-only authentication is invalid, so staging branch/ref, release history, and workflow status are not externally verified.
- This environment denies Netcup SSH before any host command, so staging SHA, runtime path, container identities, Caddy, volumes, backups, and isolation are not runtime-verified.
- No deployment, migration, seed, browser QA, DNS/Caddy change, or host mutation is authorized on this evidence alone.

## REQUIRES_LIVE_STAGING_VALIDATION

Pre-deploy checklist:

- Recover valid read-only GitHub visibility; select a reviewed exact 40-character SHA and approved branch.
- Inspect Netcup staging read-only: project path, attached branch/SHA, Compose/overrides, service identities, isolated database/volumes, backup and rollback evidence, and Caddy route. Confirm production separation.
- Confirm the existing wrapper performs Hunter-only recreation and protects n8n/Ollama/edge identities.
- Back up staging with the proven WAL-safe method and record restore evidence before migration.
- Confirm `MUNSHI_STAGING_IDENTITY=staging` exists only in the isolated staging runtime and that the resolved database path is not production.

Staging deploy/seed procedure (only after the checklist):

1. Invoke the existing manual deployment workflow/wrapper with the exact approved SHA; never use production endpoints or a push trigger.
2. Run the seed tool dry-run first with the actual isolated staging DB path. Review only counts and fixture keys.
3. Run the same command with `--apply`; verify every fixture is `TEST/STAGING`, uses `staging-fixtures.invalid`, and has no delivery/submission flags.
4. Do not enable native Answer Brain, preparation, resume shadow, or any external-action feature merely for seed testing.

Post-deploy browser QA checklist:

- Verify `https://staging-dashboard.munshi.systems/` only; do not use production dashboard.
- Confirm fixture badges/metadata are visibly staging/test-only, duplicate handling is non-destructive, and malformed/long-JD views remain safe.
- Verify ready preparation is a readiness state only and never appears as submitted, sent, emailed, or applied.
- Verify sensitive veteran/disability fixtures show only request-state boundaries, never values in ordinary job views.

Database validation checklist:

- Apply 015–023 according to the server’s approved migration runner and confirm the staging database alone changed.
- Confirm `staging_synthetic_jobs` references exactly the seeded IDs; verify real job/event/application/n8n/Telegram/outcome tables have no synthetic metrics or receipts.
- Confirm all synthetic records carry the five required metadata values and URLs stay under `staging-fixtures.invalid`.

Rollback checklist:

- Use the existing staging wrapper/backup evidence to restore the prior attached staging SHA and WAL-safe database backup if validation fails.
- Remove/reseed only the ledger-owned synthetic IDs; never use broad job-table deletion.
- Verify n8n/Ollama/edge identities and production service state did not change.

Production-protection checklist:

- Never target `dashboard.munshi.systems`, production DB paths, production volumes, production Caddy, or production n8n.
- Never send email, Telegram, outreach, browser/ATS submission, or MUNSHI Apply actions from fixtures.
- Never claim live provider, ATS, browser, or deployment proof from this local fixture validation.
