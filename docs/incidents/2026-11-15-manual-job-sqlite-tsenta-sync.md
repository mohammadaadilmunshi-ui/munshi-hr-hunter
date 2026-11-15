# MUNSHI private dashboard: SQLite write contention and Relay/Tsenta sync

**Evidence:** Read-only Netcup inspection, November 15, 2026 (UTC). **Production unchanged.**

## Production baseline

- Hunter production branch: `feat/tsenta-connector-completion-20261015` at `5d482f75c6ccd001912fbab41146a7f8eb9259ed`; not the older, locally dirty Mac checkout or the public showcase.
- Hunter API, original dashboard health, hosted Apply API, and n8n health endpoints each returned HTTP 200. Disk usage was 67%.
- SQLite ran in WAL mode; main database approximately 25 GB, 8,464 jobs. The page freelist was tiny; **do not run VACUUM, checkpoint, migrate, restart or copy the DB as part of diagnosis.** No corruption was established, and a full integrity scan of this large live database was not attempted.
- Latest predeployment DB backup found dated October 23, 2026, approximately 23 days old. **Take and verify a new non-destructive consistent backup before any production promotion.** Preserve all existing backups and runtime overlays.

## Fault evidence

1. The manual Add job code overrides the standard 30-second SQLite busy timeout with 250 milliseconds, then allows only three short backoffs. `BEGIN IMMEDIATE` can fail while unrelated workers write. The UI surfaces that exception as a save error. We did not invoke a real manual Add job during this investigation; the user's exact popup was not captured in server logs.
2. The receipt-reconciliation worker logged `sqlite3.OperationalError: database is locked` on November 15 at 23:06:25 UTC, in `reconcile_from_dashboard`. The host's retained receipt log contains 489 lock exceptions and also many successful `RELAY_RECONCILE` runs. An uncaught `sqlite3.OperationalError` in one application readback can abort an entire batch.
3. Two independent receipt pollers are configured: Hunter supervisor (default 600 seconds) and host cron (every five minutes). Cron `flock` protects only its own wrapper, not the supervisor. Coordinate to **one** canonical receipt scheduler in a separately reviewed runtime change.
4. Backend Tsenta OAuth is connected and profile-verified, with recent token refresh. 37 application-provider routes have Tsenta ownership; most recent route update is November 12. These 37 managed routes are **not** an import of the complete external provider application inventory.
5. The 12,309 privacy-minimized Tsenta observations include records through November 15 at 11:31 UTC. The host's separate 06:30 Eastern read-only learning cron is active; it temporarily opts in even though the container default `MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=false` remains correct. Another 02:00 systemd learning timer reported exit-code failures on Nov 14–15 after reaching its configured historical page cap. That failure must not be represented as a full successful historical sync; do not silently erase the page-cap alert.
6. Synchronization is therefore **partial, not absent**: current provider observations and auth exist, but reliable application-status reconciliation and all-history coverage are not proven.

## Source-level mitigation in this branch

- Manual reviewed-job saving now waits up to 5 seconds per writer-claim attempt (plus three bounded backoffs), keeping one transactional canonical duplicate guard. This fixes short transient lock failures without bypassing eligibility or creating an application submission.
- Receipt reconciliation now catches SQLite operational contention *per route*, counts the failure and proceeds to later existing routes. It does **not** retry an employer submission or claim receipt proof.
- Regression tests hold an unrelated SQLite write lock longer than the old save budget, then confirm success and dedupe; a separate test injects a readback lock and confirms the next route is still checked with zero outgoing applications.

## Release and operations gates

1. Verify a fresh consistent database backup and tested recovery path; inspect disk, database health and live worker state before any write or restart.
2. Run focused and broader tests, GitHub CI, staging concurrency and source/production SHA comparison; preserve all five production Compose layers, n8n and Ollama identity and existing job authority.
3. Promote only through the approved *manual* GitHub→Netcup exact-SHA process. Do not copy a local DB or deploy this branch merely because tests pass.
4. After approval, choose one receipt poller at the runtime layer; verify no overlapping host/supervisor execution, no queued ownership conflicts, and no duplicate sends. Do not change worker schedules without a backup and rollback.
5. Monitor the actual manual Add job UI once with a **non-sensitive** test opening only when authorized, then check stored job dedupe and no automatic application submission.
6. For provider status, compare a few existing route states to authenticated **read-only** Tsenta status; report `PROVIDER_SUBMITTED` separately from independently verified employer receipts.
7. Address the 200-page historical learning cap with explicit cursors/coverage telemetry and only supported provider queries; never claim full synchronization or promote training rules on partial evidence.
8. Record daily last successful sync, remaining route lag, batch errors, SQLite contention rate, and scheduled poller identity. Roll back the exact release on failed health, data integrity, or receipt safety checks.

**No production database mutation, manual job insertion, external application execution, credential reveal, cron edit or service restart was made during this investigation.**
