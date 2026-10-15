# V5 — Verified employer application URL evidence and two-link UI

Status: additive source-only draft PR. Production schemas, deployments, and job applications untouched.

## Verified baseline (October 15, 2026)

- Historical V1 production audit: 2,638 LinkedIn records, including 946 missing application URLs and 1,692 URLs pointing to aggregator listings.
- New V5 read-only production-data scan: 2,640 LinkedIn records (two newer jobs). Without externally supplied VERIFIED proposals, all were classified NO_VERIFIED_EVIDENCE_SUPPLIED.
- This does not imply there are no employer links. It means the evidence was not prepared for migration.
- Zero database writes, zero job submissions, zero external requests by dry-run.

## Source changes

New module: app/linkedin_verified_url_evidence_v5.py

- Optional separate append-only evidence table; explicit migration flag required.
- Stores exact LinkedIn source URL, employer job URL, employer application URL, ATS label, job identity fingerprint, verification time, source/evidence digest, and submit_authorized=0.
- Only strongly corroborated VERIFIED results may enter the controlled writer. Strict expiry, job identity, host and source checks. Never changes jobs.job_url/jobs.apply_url or application state.
- Verifies the current persisted job identity at evidence write time, rejects conflicts, and makes repeat writes idempotent.
- Read-only lookup returns unavailable if the table does not exist, evidence expired, or job identity changes.

New script: scripts/linkedin_url_backfill_dryrun_v5.py

- Read-only SQLite connection; accepts optional bounded JSON proposals and reports candidates, conflicts, invalid proofs, and absent evidence.
- No network calls, database mutation, or application submissions.

Dashboard: app/product_pages.py

- For LinkedIn jobs, shows separate Open LinkedIn listing and Open employer application (verified) actions.
- Employer link remains disabled until current VERIFIED evidence exists.
- Keeps the existing internal Prepare application action separate.
- Leaves non-LinkedIn job cards unchanged.

New and updated tests cover immutable source URLs, replay, conflict, expiry, stale job identity, dry-run no writes, safe UI buttons, and the old JobSpy direct-link contract.

## Important Prepare Application diagnostic

The internal preparation action first requires current job-evidence and Candidate Truth digest fields before creating a durable operation. This is separate from employer-link resolution. The read-only historical database aggregate recorded 62 prepare operations; one linked to LinkedIn was CANCELLED. This alone does not establish the current failure reason of a specific Prepare click. V5 does not patch Prepare or assume its problems are resolved.

## Security and release blockers

1. Establish a trusted signed employer-domain approval record with runtime verification at the actual database write boundary; current source proof flags alone are not a sufficient production trust mechanism. Likewise, corroborate non-first-party public ATS results.
2. Establish tenant and operator authorization on evidence mutations; a development allow_write flag is not an actual production authorization model.
3. Controlled staging migration, backup, rollback, and dry-run-first backfill. Production database schema is unchanged.
4. Source-SHA reconciliation, Linux/Docker CI, passing application-flow tests, explicit deploy approval.
5. Repeat employer posting checks when applying, and preserve independent receipt verification.
6. Diagnose Prepare Application separately with redacted evidence from a specific failed click.

## Safe dry-run command

python -m scripts.linkedin_url_backfill_dryrun_v5 --db /path/to/hunter.db

Optional VERIFIED proposals JSON (still no writes):

python -m scripts.linkedin_url_backfill_dryrun_v5 --db /path/to/hunter.db --proposals /path/to/proposals.json

## Rollback

These are additive changes in draft PR 103; no production schema/data changes. Revert the source changes, or leave the new modules unused. No production database restore necessary.
