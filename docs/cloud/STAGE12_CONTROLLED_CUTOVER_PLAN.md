# Stage 12 Controlled Cutover Plan — Not Authorized

- Timestamp: 2026-08-31 (America/New_York)
- Preparation baseline HEAD: `274feb4dcd1cdf780e49cf8c1b8aede168c6b584`
- Branch: `feat/cloud-migration-foundation`
- Host/OS/architecture/Docker/Compose: pending Netcup provisioning
- n8n target: `2.22.5`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Container/test/Git status: cutover not started
- Safety status: plan only; cutover forbidden in this run
- Production Mac mutation count: `0`

This document describes a future, explicitly authorized transfer of production authority. Stage 8B/9 must not execute any step below that touches production state, Mac services, credentials, Telegram, DNS, or authority.

## Preconditions

Cloud hardware, parity, persistence, reboot, 24-hour minimum endurance, backup restore, resource headroom, security exposure, and exact Git/image/workflow versions must be green. No OOM, unexplained restart, persistent health failure, database integrity error, or unresolved upstream defect may remain. The state-migration manifest, rollback snapshot, authority ledger, communication window, and human rollback decision-maker must exist.

## Controlled sequence

1. Re-run the authoritative cloud validator with synthetic state and record an exact `GO`, commit SHA, image IDs, n8n version, workflow digest, and container status.
2. At the later approved boundary, create application-consistent encrypted Mac snapshots according to Stage 10. Do not mutate source databases merely to make a copy.
3. Pause Mac mutation in a defined order: new discovery/scheduling, coordinator, n8n triggers, callbacks, then Telegram polling/sending. Record the final queue, receipt, update, execution, and dedupe cursors. The Mac remains recoverable and is not retired.
4. Confirm quiescence twice across a meaningful interval. Any new mutation cancels the boundary and requires a fresh snapshot.
5. Transfer encrypted state and secrets, verify ciphertext and plaintext checksums at the correct trust boundaries, restore into stopped cloud volumes, enforce permissions, and run database integrity/row-count checks.
6. Start cloud services with every external production lane still disabled. Validate internal reads, credential decryption, canonical workflow uniqueness, queues, callbacks, dedupe state, Playwright, and HR scoring without sending or discovering.
7. Prevent duplicates using the migrated authority ledger: one Telegram polling/sending lease, one discovery/scheduler lease, one canonical n8n trigger authority, and stable callback/outbox idempotency keys. Legacy workflow `GfiDMrb94BFJXq1D` remains inactive.
8. With an explicit cutover authorization in force, activate cloud authority one lane at a time. Start callback acceptance/internal n8n routing, then scheduler/discovery only when its Mac counterpart is confirmed paused, and Telegram last only after the Mac update/send authority is definitively released.
9. Validate bounded real behavior: one controlled item, expected database mutations, exactly-once n8n execution/callback, and exactly-once Telegram behavior. Observe logs/resources and reconcile IDs end to end.
10. Hold immediate rollback capability. On duplicate behavior, incorrect state, health degradation, credential failure, or external side effects, disable cloud authorities first, reconcile the ledger, and restore the prior single authority according to the rollback runbook.
11. Only after an explicitly defined stabilization window and human acceptance may Mac authority be retired. Retirement is a separate destructive/operational decision and is never implied by cloud success.

## Rollback invariant

At all times there is at most one production authority per lane. Rollback never starts a Mac sender/scheduler while the cloud counterpart can still run. DNS, if later used, changes only after application authority is correct and with a prepared TTL/rollback. Cloud databases are never blindly copied over the preserved Mac baseline.

## Current status

```text
PRODUCTION_MAC: UNCHANGED
PRODUCTION_MAC_MUTATIONS: 0
PRODUCTION_STATE_MIGRATION: NOT_STARTED
TELEGRAM_CLOUD_PRODUCTION: DISABLED
DISCOVERY_CLOUD_PRODUCTION: DISABLED
CUTOVER: NOT_STARTED
```
