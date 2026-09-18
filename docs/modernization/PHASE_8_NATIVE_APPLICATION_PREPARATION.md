# Phase 8 — Native Application Preparation

The Phase 8 local foundation is feature-gated by
`MUNSHI_NATIVE_APPLICATION_PREPARATION_ENABLED` and is off by default.  It
creates a versioned, tenant-owned, idempotent readiness snapshot for a job that
has an explicit owner association.  The snapshot contains only safe reference
metadata: an explicit Master Resume reference, answer-resolution metadata,
policy outcome, and contact provenance metadata.  It deliberately excludes
answer values, contact email values, inferred patterns, credentials, and any
external action mechanism.

Readiness is deterministic.  Only eligibility, opportunity policy, Master
Resume/files, requested answers, account state, permissions, duplicate state,
and open-job state can make a record `READY_TO_APPLY`.  That is a preparation
state only; it has no execution authority.  Missing information remains
`NEEDS_INPUT`; an explicit unavailable dependency is `BLOCKED_EXTERNAL`.

There are no routes, UI controls, Apply integration, n8n callbacks, email
operations, browser automation, credentials, or mutation of legacy product
job state.  The existing n8n writer/callback path remains authoritative.
# Verification update

The feature remains preparation-only. A first-use SQLite nested-connection writer lock was repaired by committing Phase 8 schema/default-owner work before the independent Career Policy advisory lookup. This preserves the existing transaction boundaries while avoiding a local lock cycle. Dedicated tests pass, including empty/absent required-answer `NEEDS_INPUT`, idempotency, tenant isolation, and no-submission semantics.
