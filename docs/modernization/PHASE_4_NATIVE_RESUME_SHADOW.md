# Phase 4 — Native resume shadow foundation

This phase adds an internal, tenant-scoped planning and comparison-telemetry ledger. It is disabled by default, has no authority path, and does not call n8n, create jobs, queues, callbacks, artifacts, or modify Master Resume/candidate-artifact records.

The planner accepts only a candidate-owned `candidate_artifacts` reference with a matching historic n8n result for the requested job. Its exact-term ledger is deterministic. It uses only explicitly confirmed, evidenced, non-sensitive digital-twin facts in a prompt plan; self-identification facts are excluded. A plan with missing evidence is `NEEDS_INPUT`; a plan with sufficient evidence is `BLOCKED_EXTERNAL` because no model client or physical renderer is present. It never reports a generated artifact, success, page count, or integrity result that does not exist.

Shadow and authority flags default off. Authority remains hard-disabled in this foundation even if an environment variable is supplied. Records are owner-scoped and idempotent by owner plus idempotency key, with defensive composite joins on reads.

Physical document rendering and actual PDF page-count verification remain **BLOCKED_EXTERNAL** pending a separately reviewed renderer/template boundary. Native-vs-n8n parity evaluation, live model invocation, and any authority promotion are intentionally out of scope.

Rollback: disable `MUNSHI_NATIVE_RESUME_SHADOW_ENABLED` and stop calling the internal planner. The migration is additive and does not alter historic n8n rows or production workflow authority.
