# Phase 5 — Answer Brain

The tenant-owned answer vault is feature-gated by
`MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED` (off by default). It has no UI,
routes, AI client, Apply/n8n bridge, ranking, or resume integration.

Normal answers are constrained by family, source, confidence, confirmation,
autofill, and canonical JSON conditions. Resolution uses a confirmed stored
autofill answer, then exact confirmed candidate evidence, otherwise
`NEEDS_INPUT`; it never guesses.

Voluntary self-ID is rejected from the normal plaintext table. A separate
AES-GCM vault binds version, tenant, user, row ID, and category in canonical
JSON AAD. Normal projections and `planning_input` never include self-ID.
Migration 019 is additive. Rollback is disabling the flag.
