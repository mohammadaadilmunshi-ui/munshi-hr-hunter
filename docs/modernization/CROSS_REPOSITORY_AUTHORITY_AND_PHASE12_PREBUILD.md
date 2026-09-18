# MUNSHI Sections 1–3 Foundation Contract

**Status:** STRENGTHENED PRE-BUILD / NON-RUNTIME
**Hunter Section 1 staging baseline:** `4f11506c8ae028e88e7de034da1a20760fa2c475`
**Hunter working branch:** `feat/cross-repo-authority-phase12-v1`
**Apply Phase 9 reference SHA:** `100fa7b1053a2a030743791ab4a42e9e283ed7f6`
**Scope:** Section 1 Candidate Truth Profile integrity + Section 2 authority reconciliation + Section 3 inert Phase 12 cross-repository contracts.

## 1. Why Sections 1–3 are one foundation block

Section 1 established the permanent Hunter-owned Candidate Truth Profile. Sections 2 and 3 must consume that exact truth rather than create another editable copy of it.

The strengthened architecture therefore has one continuous chain:

`confirmed Master Resume evidence -> encrypted candidate edits/details -> deterministic Hunter profile snapshot -> signed preparation handoff -> Apply execution boundary -> correlated Apply receipt -> Hunter CRM projection`

No later section may bypass this chain by inventing a parallel profile, application state, submission flag, credential store, or uncorrelated event path.

This document does **not** authorize HTTP delivery, browser execution, ATS/provider login, application submission, credential use, Gmail sending, n8n runtime changes, staging promotion, production deployment, or production mutation.

## 2. Non-negotiable invariants

1. Hunter is the canonical career/candidate truth system.
2. MUNSHI Apply is the canonical browser/application-execution system.
3. n8n may orchestrate work but is not a competing truth store.
4. Master Resume evidence is immutable after confirmation.
5. Candidate edits are separately attributable and encrypted.
6. Candidate-entered work authorization/preferences/self-ID are encrypted and separately revisioned.
7. `PREPARED != SUBMITTED`.
8. `READY_TO_APPLY != SUBMITTED`.
9. `HANDOFF_ACCEPTED != SUBMITTED`.
10. Gmail/recruiter messages are evidence, not browser submission authority.
11. Only verified Apply execution evidence may assert browser/provider submission.
12. Protected fact plaintext never crosses the generic repository bridge.
13. Unsupported ATS/provider/security situations remain explicit non-submitted states.
14. Cross-repository messages are tenant/user bound, versioned, digestible, replay-safe, and fail closed.
15. A reverse receipt must correlate to the exact accepted Hunter handoff, not merely a similar application.

## 3. Section 1 — Candidate Truth Profile integrity

### 3.1 Immutable evidence

The confirmed Master Resume extraction remains the immutable evidence baseline. Its stable bindings are:

- `extraction_id`
- `profile_sha256`
- `source_sha256`
- tenant/user ownership
- confirmed status

The Phase 12 projection recomputes the profile hash before export and fails closed if the supplied confirmed evidence no longer matches its stored hash.

### 3.2 Encrypted candidate overrides

Candidate-confirmed edits are stored as AES-GCM encrypted override data, never by mutating the confirmed extraction.

The override envelope carries a monotonic `revision`. Strengthening added an important invariant: resetting the last override or resetting all overrides no longer deletes the encrypted revision envelope. Returning to the original evidence is itself a newer state and must not appear to revert to revision zero.

### 3.3 Encrypted candidate-entered application details

Work authorization, sponsorship, work preferences, availability, accommodations and voluntary self-ID remain encrypted-only candidate-entered truth.

The V3.1 details store is now wrapped in a backward-compatible encrypted revision envelope:

- `schema_version = candidate-profile-details-envelope-v1`
- `revision`
- `updated_at`
- `values`

Legacy encrypted V3.1 payloads are read as revision `0` and upgraded on the next confirmed save. No plaintext migration is introduced.

### 3.4 Composite profile revision

The cross-repository profile revision is scoped to one immutable source extraction:

`revision_scope = SOURCE_EXTRACTION`

It is derived from the two encrypted Section 1 counters using the same Cantor-pairing rule in Hunter and Apply:

`profile_revision = ((override_revision + candidate_details_revision) * (override_revision + candidate_details_revision + 1) / 2) + candidate_details_revision + 1`

Both repositories reject a snapshot whose declared `profile_revision` does not match those two revision components.

A new immutable Master Resume extraction begins a new `source_extraction_id` scope, so revision ordering is never incorrectly compared across different evidence baselines.

## 4. Section 2 — single-writer authority matrix

| Concept | Canonical writer | Secondary role | Required rule |
|---|---|---|---|
| Candidate/Profile truth | **Hunter** | Apply consumes read-only projection/cache | Apply must not independently overwrite career truth |
| Master Resume evidence | **Hunter** | Apply consumes hashes/references | Original confirmed extraction remains immutable |
| JD-specific resume artifact | **Hunter** | Apply selects/uploads exact artifact | Exact artifact hash must be recorded |
| Candidate recurring answer defaults | **Hunter** | Apply maps truth to observed controls | Sensitive/protected values remain reviewable and provenance-aware |
| Page-specific question/control semantics | **Apply** | Hunter may receive unresolved requirements | Browser-observed wording/control identity remains Apply-owned |
| Generated free-text review | **Apply** | Hunter supplies truth/evidence | Generated content cannot invent unsupported facts |
| Job/preparation identity | **Hunter** | Apply stores immutable linkage | `preparation_id` remains stable correlation input |
| Browser application identity | **Apply** | Hunter stores returned projection | Runtime session/application correlation is Apply-owned |
| ATS family/page identity | **Apply** | Hunter may coarse-classify preparation URL | Browser-observed ATS identity wins |
| ATS credential operation | **Apply/native companion** | Hunter legacy/foundation metadata may migrate later | Secret plaintext never crosses bridge |
| Eligibility/work-preference policy | **Hunter** | Apply consumes resolved inputs | Browser runtime cannot re-author career truth |
| AutoPilot/runtime policy | **Apply** | Hunter may request preparation only | Final submit/security policy remains Apply-owned |
| Browser session/checkpoint state | **Apply** | Hunter receives summarized status | CAPTCHA/MFA/OTP/manual checkpoints cannot be inferred away |
| Submission/execution receipt | **Apply** | Hunter validates and projects | Ready/attempted/submitted/confirmed/failed remain distinct |
| Career CRM state | **Hunter** | Apply emits verified events | Hunter advances from evidence, never handoff acceptance alone |
| Gmail/recruiter evidence | **Hunter** | Apply may later consume normalized evidence | Email cannot fabricate browser execution truth |
| Orchestration | **n8n, current architecture** | Hunter/Apply expose bounded capabilities | n8n coordinates; it is not canonical profile/application truth |

## 5. Existing Phase 9 seam preserved

Hunter already creates the signed inert envelope:

`munshi-apply-preparation-handoff-v1`

Canonical fields remain:

- `version`
- `handoff_id`
- `tenant_id`
- `user_id`
- `preparation_id`
- `application_id`
- `job`
- `provider`
- `state`
- `artifact_references`
- `answers`
- `provenance`

Hunter signs the canonical body with freshness-bound HMAC headers. Apply verifies signature/freshness/body digest, normalizes the package into its local handoff ledger, enforces idempotency, and explicitly treats acceptance as **not** a provider action or submission.

Phase 12 extends this seam; it does not replace or rename it.

## 6. Section 3 — actual Section 1 → Apply projection

### 6.1 Candidate Profile Snapshot — Hunter → Apply

`app/profile_snapshot_projection.py` now constructs the snapshot from the real Section 1 stores rather than accepting an arbitrary caller-authored profile.

Export requires:

- encrypted Candidate Truth Profile storage available;
- a `CONFIRMED` Master Resume extraction;
- active tenant/user matching the extraction owner;
- valid immutable profile hash;
- valid source resume/profile SHA-256 bindings.

Snapshot identity includes:

- `contract_version`
- `authority = munshi-hr-hunter`
- `projection_mode = READ_ONLY`
- `revision_scope = SOURCE_EXTRACTION`
- `tenant_id`
- `user_id`
- `profile_id`
- `profile_revision`
- `override_revision`
- `candidate_details_revision`
- `source_extraction_id`
- `source_profile_sha256`
- `source_resume_sha256`
- `generated_at`
- `facts`
- `profile_digest`

Untouched resume facts are marked `DOCUMENT_CONFIRMED`; candidate-overridden sections are marked `USER_CONFIRMED` with override revision provenance.

Protected work-authorization/self-ID values are represented only through opaque Hunter vault references. Their plaintext values are not placed in the generic bridge payload.

### 6.2 Deterministic profile digest

The content digest intentionally excludes observational `generated_at` and the digest field itself.

Facts are canonicalized into deterministic key/id order. Therefore:

- repeated exports of unchanged truth produce the same `profile_digest`;
- a truth change changes the digest;
- a source evidence hash change changes the digest;
- changing only export time does not change the digest.

Duplicate fact IDs and duplicate semantic fact keys fail closed.

### 6.3 Resume Artifact Contract — Hunter → Apply

A prepared resume is bound to both the application preparation and the exact Candidate Truth Profile state.

Minimum binding:

- `artifact_id`
- `kind`
- `sha256`
- `mime_type`
- optional `size_bytes`
- `source_preparation_id`
- `source_extraction_id`
- `profile_revision`
- `profile_digest`
- `job_id`

Apply must never silently substitute an artifact with a different hash/profile binding.

### 6.4 Application Execution Receipt — Apply → Hunter

The reverse receipt carries:

- `contract_version`
- `source = munshi-apply`
- `event_id`
- `correlation_id`
- `tenant_id`
- `user_id`
- `handoff_id`
- `handoff_body_sha256`
- `preparation_id`
- `application_id`
- Apply runtime application identity
- provider
- event type
- `occurred_at`
- evidence payload

Before CRM projection Hunter verifies the receipt against the exact expected:

- tenant
- user
- handoff ID
- accepted handoff body SHA-256
- preparation ID
- application ID

No fuzzy matching is permitted.

### 6.5 Submission semantics

Only explicit Apply events equivalent to:

- `APPLICATION_SUBMITTED`
- `APPLICATION_CONFIRMED`
- `APPLICATION_COMPLETED`

may assert submission progress, and each must carry the required evidence.

`APPLICATION_SUBMITTED` requires both `submit_attempted = true` and `submit_succeeded = true`.

Confirmed/completed events require `confirmation_observed = true`.

Conversely, non-submission events are rejected if they try to smuggle `submit_succeeded = true`, and non-confirmation events are rejected if they try to smuggle `confirmation_observed = true`.

## 7. CRM/Gmail projection rules

Hunter owns the outward career CRM projection.

- Hunter prepared package -> `PREPARED` / `READY_TO_APPLY` / `NEEDS_INPUT`
- Apply accepted package -> `HANDOFF_ACCEPTED` only
- Apply reaches review boundary -> `READY_FOR_REVIEW` or equivalent non-submitted state
- verified Apply `APPLICATION_SUBMITTED` -> Hunter may project `SUBMITTED`
- verified Apply confirmation/completion -> Hunter may strengthen to confirmed-submitted state
- Gmail/recruiter confirmation without matching Apply submission receipt -> attach evidence and reconcile; never fabricate browser submission
- recruiter interview/assessment/rejection/offer -> update downstream CRM outcome evidence without rewriting the original browser execution receipt

## 8. Adapter/deprecation plan

### Keep and extend

- Hunter immutable Master Resume evidence
- Hunter encrypted Candidate Truth Profile
- Hunter signed Phase 9 handoff
- Apply `CareerOSHandoffConsumer`
- Apply browser/session/checkpoint execution machinery
- Apply event vocabulary
- current n8n orchestration authority until an explicit later migration

### Needs adapter/runtime wiring later

- inert Hunter profile snapshot -> Apply local read-only profile cache
- exact resume artifact contract -> Apply upload verification
- Hunter answer defaults -> Apply semantic question resolver
- Apply execution receipt -> Hunter CRM transition persistence
- Apply ATS runtime identity -> Hunter application projection

### Deprecate only after migration proof

- independently editable Apply profile truth that conflicts with Hunter canonical truth
- Hunter operational ATS credential ownership once Apply/native credential boundary is proven
- duplicate application-state fields capable of advancing independently

No destructive migration belongs in Sections 1–3.

## 9. Integrated regression contract

The strengthened Hunter gate compiles and tests Sections 1–3 together, including:

- immutable evidence and Profile workspace regressions;
- encrypted override revision monotonicity after resets;
- candidate-details legacy compatibility and revision advancement;
- real Section 1 -> Phase 12 projection;
- protected-value non-disclosure;
- stable content digest behavior;
- duplicate fact rejection;
- profile revision equation enforcement;
- artifact SHA/profile binding;
- reverse receipt correlation;
- submission-event evidence requirements;
- no native submission authority;
- repository secret/state guards.

Apply validates the same profile revision formula and wire vocabulary and runs repository safety, lint, typecheck/tests/build, native-host tests, migration tests, security, browser tests and owner-workspace validation.

## 10. Gate before Section 4

Sections 1–3 may be treated as the locked foundation only when:

1. Hunter combined Sections 1–3 CI is green.
2. Apply combined contract/native/browser/security/migration CI is green.
3. Both PRs remain reviewable and no temporary diagnostic workflow remains.
4. No staging or production deploy occurs from the pre-build branches.
5. No real browser application, provider login, credential retrieval, email sending or n8n runtime mutation has occurred.
6. Section 4 consumes these contracts rather than creating a parallel truth or state path.

Until all six conditions hold, work does not advance to Section 4.
