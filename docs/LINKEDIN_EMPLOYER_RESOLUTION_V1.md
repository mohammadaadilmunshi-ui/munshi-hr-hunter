# LinkedIn to employer application URL resolution V1

Status: source-only, read-only diagnostic prototype. Not wired to production UI,
not a submission provider, and not a production migration.

## Problem and measured baseline (2026-10-15)

On Netcup Hunter b8cd45ea0390, 2,638 records have a LinkedIn source.
946 had a null or empty apply_url; the other 1,692 had a LinkedIn URL
identical to their listing URL. No external application URL was present in
these 2,638 records.

The JobSpy LinkedIn parser relies on the code#applyUrl element, which
is not reliably supplying employer URLs.

## New source-only components

- app/linkedin_employer_link_resolver_v1.py: Conservative cross-source matches,
  URL safety checks, typed resolution results, and public ATS response adapters.
- app/linkedin_public_ats_feed_v1.py: bounded GET-only public APIs for
  Greenhouse, Lever, Ashby, SmartRecruiters; fixed hosts and known boards only.
- scripts/linkedin_link_resolution_dryrun_v1.py: SQLite audit, read-only,
  without network requests or database mutation.
- Tests cover identities, ambiguity, safe URLs, public API adapters,
  and immutable source URLs.

## Status semantics

UNKNOWN: No trustworthy external candidate.
CANDIDATE: Possible external match; no automatic submission.
AMBIGUOUS: Multiple possible employer destinations.
VERIFIED: Unique published official ATS record with corroborating requisition,
  or description plus matching location evidence.
UNAVAILABLE: Invalid listing/known board mismatch.
CLOSED / EASY_APPLY: Reserved for later explicit evidence. Never inferred
  merely because no external URL exists.

Neither HTTP 200 nor company/title similarity alone proves a job is the
same employer application. Do not automate LinkedIn Apply clicks or bypass
LinkedIn access controls.

## Read-only audit

python -m scripts.linkedin_link_resolution_dryrun_v1 --db /path/to/hunter.db

No network requests or writes. Public feed functions are invoked separately
only when employer/board identity is already verified.

## Deferred integration (not part of this code tranche)

1. Add evidence persistence and dry-run-first idempotent backfill, without
   changing historical LinkedIn job IDs or application lifecycle records.
2. Add employer-site lookup for companies without confirmed ATS boards.
   Restrict to documented/public ATS and employer jobs; no guessed URLs.
3. Show two separate buttons: LinkedIn source and employer application.
   Keep Prepare Application as a separate internal action.
4. Validate exact employer/job IDs, post status and destination before submitting.
5. Only then provide the verified URL to MUNSHI Apply or a Tsenta adapter.

## Production impact, controls and rollback

No production schema or route changes; no deployments, no applications.
Rollback is leaving these isolated modules unused or reverting this PR.
A future deployment requires exact-source-SHA CI, staging tests, and a
deliberate production promotion. Passing tests here does not imply
production release readiness.
