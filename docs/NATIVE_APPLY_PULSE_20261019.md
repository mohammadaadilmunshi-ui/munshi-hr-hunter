# MUNSHI Apply Pulse — Job Cards

The Pulse is a dynamic, evidence-based MUNSHI Native *application workflow readiness index*. It is NOT a calibrated probability of submission, employer acceptance, or hiring.

## Job card
- Compact green indicator: MUNSHI Apply Pulse · NN% · Learning / Well practiced / Live-tested.
- Missing verified employer destination -> Growing, with no fabricated percentage.
- Separate from Hunter Match and Relay provider badge. Applies only to Native.

## Scoring
- 34 foundational Native preparation points + 18 from a verified, non-aggregator employer destination (required to show number).
- 10 for identifiable ATS; up to 8 for distinct observed application refs (log-scaled).
- Up to 13 for unique employer form examples; up to 5 for observed control kinds.
- Up to 12 for independent receipt-digest-backed NATIVE employer confirmations.
- Without independent Native receipts, cap at 86; with such receipts, cap at 96.
- Public vendor marketing or Relay completed statuses give NO points for Native success.
- Version: munshi-native-apply-pulse-v1. Values are index weights, not probabilities.

## Safety
- Read-only, tenant-scoped stored evidence; no schema migration, submission, credentials, user answers or hidden per-company rules.
- Computed once per job grid, with fail-soft fallback if learning evidence temporarily unavailable.
- Verified Native outcomes increase the score as real employer receipts accumulate.
- Run CI and synthetic browser checks, deploy by guarded image-only release, then verify original dashboard.
