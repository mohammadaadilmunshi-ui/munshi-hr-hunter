# Phase 9 — MUNSHI Apply Preparation Handoff

`app.apply_handoff` is a disabled-by-default, local-only bridge boundary from a
tenant-owned Phase 8 preparation snapshot. It creates canonical HMAC-SHA256
envelopes only when both `MUNSHI_APPLY_HANDOFF_ENABLED` and a configured
`MUNSHI_APPLY_HANDOFF_HMAC_SECRET` are present. The secret is never persisted
or logged.

The additive `apply_preparation_handoffs` ledger records package provenance,
digest, tenant/user ownership, provider classification, and replay-safe receipt.
It contains only references/resolution metadata: it does not copy answer-vault
plaintext. Provider detection recognizes Greenhouse, Lever, Ashby,
SmartRecruiters, and Workday URL shapes; unknown shapes are `UNSUPPORTED_SAFE`.

`sign_transport` produces (but never transmits) the shared receiver-compatible
canonical JSON bytes and `X-Munshi-Event-Id`, timestamp, content-SHA256, and
HMAC-SHA256 signature headers. Its MAC binds `handoff_id.timestamp.body_hash`;
the receiver enforces freshness and treats `handoff_id` as the replay identity.

The only bridge states are `PREPARED`, `NEEDS_INPUT`, `READY_TO_APPLY`, and
`HANDOFF_ACCEPTED`. Handoff acceptance means a local package was authenticated;
it is not a browser action, provider request, n8n invocation, or submission.
`SUBMITTED` is intentionally absent. n8n remains authoritative.

Focused tests cover disabled default, HMAC rejection, strict malformed payload
rejection, tenant binding, replay idempotency, unresolved-answer preservation,
and absence of transport/authority imports. A dedicated clean Apply
continuation clone contains the matching test-only consumer at commit
`100fa7b1053a2a030743791ab4a42e9e283ed7f6`; it accepts these exact canonical
payload bytes plus freshness-bound transport headers. The dirty Apply workspace
was not modified, and no live provider is accessed by either side.
