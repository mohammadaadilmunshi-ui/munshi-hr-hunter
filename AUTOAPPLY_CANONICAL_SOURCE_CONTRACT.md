# Canonical AutoApply source contract

Status: ACTIVE. This file applies to the current source lineage and release gates.

## Purpose

Real-employer acceptance runs are discovery harnesses. Findings must be promoted into provider-neutral, candidate-neutral source behavior. Production overlays, one-job environment pins, company/job IDs, and one-off selectors are not canonical fixes.

## Canonical customer flow

`prepare package -> one package review -> one Approve & Submit -> unattended browser/account/question execution -> internal integrity snapshot -> exactly-once submit -> independent receipt verification`

After the single Approve & Submit action, no customer-facing review, Submit, Final Approve, Continue, or browser-resume prompt may appear.

## Canonical runtime rules

- Candidate facts come from Candidate Truth / Answer Vault / protected policy. Never embed candidate values in provider adapters.
- Provider recipes are optimization hints. Generic DOM/ARIA execution remains the fallback.
- Resume reuse is binding-driven by current Candidate Truth, job snapshot, artifact digest, and freshness. There is no job-scoped resume pin.
- Runtime questions produce immutable Application Plan revisions. A superseded plan is never replayed.
- File upload success requires browser-side digest proof. DOM presence or a filename is not proof.
- SPA rerenders may preserve an already browser-verified upload proof only within the same current plan/session.
- Normal runtime questions are resolved autonomously when current truth/policy supports them.
- CAPTCHA, SMS MFA, passkey, biometric, and identity verification are protected human checkpoints.
- Account creation and ordinary email verification are autonomous execution responsibilities when provider policy permits.
- A browser/form snapshot created after the user approval is internal integrity evidence, not a second user review.
- Final submit remains exactly-once and requires canonical authority plus independent employer-facing verification.
- `created`, `accepted`, an unrelated POST, a DOM mutation, or a generic object ID alone are never verified-submission proof.

## Retired / forbidden source patterns

Canonical application runtime must not contain:

- employer/job-specific names, IDs, URLs, selectors, or environment pins;
- `MUNSHI_PREPARE_PIN_RESUME_VERSION_ID` or equivalent one-job artifact pins;
- manual production calls to `drive_operation_once`;
- customer-facing post-approval review/submit/continue gates;
- provider-only final-submit verifiers when the generic binding verifier can be used;
- optimistic file-upload verification;
- runtime overlay directories as the release source of truth.

Historical code may remain in Git history for archaeology, but it must not be reachable from the active product path.
