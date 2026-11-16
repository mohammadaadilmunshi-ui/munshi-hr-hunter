# MUNSHI Relay/Tsenta: fair, single-flight receipt polling
Evidence date: 2026-11-15. Source-only; not released to staging/production.

## Live read-only finding
22 existing Tsenta-owned, claimed applications were eligible for receipt
reconciliation. The legacy SQL used descending updated_at and LIMIT.
The five-minute host cron uses a limit of 10 and the optional Hunter supervisor
uses 5, so older routes can remain permanently outside each query window if no
route status changes. Retained logs had repeated 10/10 identical result counts
and also hundreds of SQLite lock exceptions; this does not establish that every
older route is up to date. The source also scheduled two independent
receipt polling lanes with no common exclusion.

## Fix
A single per-owner, SHA-256 pseudonymized cursor file in the existing
Hunter database volume stores only the last processed internal job ID and
schema version, not a list of applications, account data, or answers. A non-blocking kernel advisory file lock ensures
host cron and Hunter supervisor cannot perform overlapping readback batches.
Each successful lock claim rotates a bounded, job-ID-ordered window of
existing claimed jobs, even when jobs are inserted or removed;
no new job/application discovery, provider application creation, form submit,
retry, OTP or employer communication is authorized by this component.
A failure advances the cursor so it does not starve other jobs; subsequent
cycles revisit the failed job. Cursor changes do not modify SQLite records
or application authority.

When a second poller cannot acquire the shared lock, it reports
already_running=1; the Hunter receipt supervisor also avoids the
notification-outbox write. The cursor remains owner-isolated and persistent
across restarts. The existing per-route error handling and receipt
verification rules remain unchanged.

## Scope / release gates
- This PR is stacked on PR #165's manual-job and SQLite readback recovery.
- Unit tests confirm rotation of all 22 jobs, exclusive lock behavior,
  owner-scoped files, status-worker outbox skip, and zero applications sent.
- GitHub Linux, Docker, product, and staging acceptance CI required.
- No staging or production deploy until an explicitly approved release branch
  satisfies GitHub staging environment branch protection.
- Production SQLite backup must be verified; don't stop or recreate n8n/Ollama,
  copy old databases or bypass release governance.
- After deployment, verify the cursor file is created under the correct data
  volume with restrictive permissions, confirm distinct receipt routes are
  polled over several cycles, monitor already_running and SQLite errors,
  and compare provider-reported statuses to independent employer receipts.
