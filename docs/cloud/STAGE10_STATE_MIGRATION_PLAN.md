# Stage 10 State Migration Plan — Not Authorized

- Timestamp: 2026-08-31 (America/New_York)
- Preparation baseline HEAD: `274feb4dcd1cdf780e49cf8c1b8aede168c6b584`
- Branch: `feat/cloud-migration-foundation`
- Host/OS/architecture/Docker/Compose: pending Netcup provisioning
- n8n target: `2.22.5`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Container/test/Git status: not applicable until Stage 9 is proven
- Safety status: plan only; hard stop before production access
- Production Mac mutation count: `0`

This plan is deliberately non-executable during Stage 8B/9. It requires later explicit human authorization because every source artifact is production state or a production secret. Production Mac mutation count remains `0`.

## Scope and authorities

The migration set is Hunter `hunter.db`; n8n `database.sqlite` and any active WAL/SHM state; the matching n8n encryption key; application/API, Telegram, and Google secrets; browser/runtime state that is separately proven necessary; and scheduler/dedupe cursors. The canonical Git workflow is not a state-migration input and remains immutable.

Before any snapshot, record source and target Git SHAs, schema versions, SQLite versions, n8n 2.22.5, file owners/modes/sizes, active workflow IDs, scheduler authority, Telegram authority, pending queue/outbox counts, and a monotonic cutover boundary timestamp. Reject unclassified files.

## Consistent snapshot

1. At a later approved maintenance boundary, prevent new mutations without terminating the only recoverable session. Pause schedulers, discovery, Telegram senders, callback ingestion, and n8n workflow starts in the documented cutover order.
2. Confirm queues are drained or record every unfinished item and idempotency key. Do not merely copy a live SQLite main file while WAL writers remain active.
3. Use Python's `sqlite3.Connection.backup()` or SQLite `.backup` from a read transaction for Hunter. Run `PRAGMA wal_checkpoint` only if separately assessed safe at the paused boundary; it is not required for the online backup API.
4. Stop or quiesce n8n only after workflows are inactive. Use n8n-supported export/state procedures and SQLite `.backup`, preserving the matching encryption key separately.
5. Run `PRAGMA quick_check` and `PRAGMA integrity_check` on each snapshot, record row counts for authority tables, then compute SHA-256. Never repair a source database in place during migration.

## Encryption and transfer

Create a manifest containing names, sizes, digests, schema versions, permissions, snapshot boundary, and no secret values. Encrypt each state bundle before it leaves the Mac with an explicitly chosen human-controlled recipient. Transfer over the dedicated SSH key using `scp`/`rsync` with host-key verification into `/opt/munshi/backups/staging/<boundary>/`, mode 0700. Transfer encryption keys/secrets as a separate encrypted bundle. Verify ciphertext digest after transfer, decrypt only into `/opt/munshi/secrets` or a protected restore staging directory, and remove plaintext staging only after the restore is proven and the approved retention rule applies.

Target permissions are `munshi:munshi`, directories 0700/0750 as classified, secret files 0600, and service database files no broader than 0600. No secret value may appear in terminal output, reports, Git, Docker inspect output, or process arguments.

## Restore and validation

Restore only into stopped, empty cloud volumes. Preserve the previous synthetic shadow volumes as rollback evidence. Verify manifest digests before and after placement; run integrity checks and recorded row-count comparisons; install the matching n8n encryption key before n8n reads encrypted credentials; start n8n/Hunter with all production execution flags still off; and validate read-only UI/API queries before permitting any mutation.

Test n8n credential decryption without sending external traffic. Validate pending execution and webhook state. Confirm the canonical workflow ID is the intended authority and the legacy workflow is inactive. Run the Stage 9 validator in a migration-aware mode that still forbids sends/discovery.

## Duplicate prevention and single authority

- Telegram: use one durable authority lease. Cloud Telegram remains disabled until the Mac listener/sender is confirmed paused at the boundary, the last update ID/message ledger is migrated, and a human-authorized single toggle transfers the lease. Never let both systems poll or send.
- Scheduling/discovery: migrate last-run/cooldown/dedupe state, then transfer a single scheduler authority lease. Cloud scheduler/coordinator remain false until Mac scheduling is paused and verified.
- n8n: only canonical workflow `L1u2xZkgFpi7KEuv` may become active. Keep legacy `GfiDMrb94BFJXq1D` inactive and prevent duplicate webhook/scheduled triggers. Reconcile pending executions by stable request/queue/execution IDs.
- Callbacks/outbox: retain idempotency receipt keys and delivery claims. Reconcile every in-flight item; never replay by timestamp alone.

## Rollback

Before cloud mutation, preserve the encrypted source snapshot, manifest, cloud synthetic volumes, and exact code/image versions. A rollback disables every cloud production lane first, verifies zero cloud senders/schedulers, restores Mac authority only from its unchanged state or the verified snapshot as appropriate, reconciles the boundary ledger, and then resumes one authority. Do not copy partially mutated cloud databases back onto the Mac without a separately reviewed reverse-migration procedure.

## Stage 10 stop gate

Required evidence before proceeding: approved maintenance window; explicit production-state authorization; successful recent cloud endurance/reboot proof; encrypted-transfer recipient; source/target free-space checks; tested restore; duplicate-prevention ledger; rollback owner; and an operator present with Netcup console recovery. Without all evidence, result is `NO_GO_STATE_MIGRATION`.

`PRODUCTION_STATE_MIGRATION: NOT_STARTED`
