# MUNSHI Cross-Repository Authority + Phase 12 Pre-Build Contract

**Status:** PRE-BUILD / NON-RUNTIME
**Hunter base SHA:** `4f11506c8ae028e88e7de034da1a20760fa2c475`
**Hunter branch:** `feat/cross-repo-authority-phase12-v1`
**Apply Phase 9 reference SHA:** `100fa7b1053a2a030743791ab4a42e9e283ed7f6`
**Scope:** MUNSHI HR Hunter ↔ MUNSHI Apply authority reconciliation and inert Phase 12 contract design.

## 1. Purpose

Sections 2 and 3 establish a single-writer architecture before any additional cross-repository runtime behavior is introduced.

The two repositories already contain overlapping concepts for profile facts, evidence, resume artifacts, application answers, application identity, ATS/account metadata, and application state. The safe design is therefore not to create a new third truth store. It is to assign one authoritative writer for each class of state and exchange immutable, tenant-bound contract messages between systems.

This document does **not** authorize network transport, browser execution, provider login, application submission, email sending, credential use, production deployment, or production mutation.

## 2. Non-negotiable invariants

1. `PREPARED != SUBMITTED`.
2. `READY_TO_APPLY != SUBMITTED`.
3. `HANDOFF_ACCEPTED != SUBMITTED`.
4. A Gmail confirmation or recruiter email is evidence, not browser submission authority.
5. Only a verified MUNSHI Apply execution/submission receipt may assert that browser/provider submission occurred.
6. Hunter remains the canonical career/candidate truth system.
7. MUNSHI Apply remains the canonical browser/application-execution system.
8. n8n may orchestrate work but is not a competing truth store.
9. Secret plaintext never crosses repository contracts.
10. Master Resume evidence remains immutable; explicit candidate edits remain separately attributable.
11. Cross-repository messages are tenant/user bound, versioned, digestible, replay-safe, and fail closed.
12. Unsupported ATS/provider situations remain `NEEDS_INPUT` or another explicit non-submitted state.

## 3. Existing Phase 9 seam that must be preserved

Hunter already creates the signed inert envelope version:

`munshi-apply-preparation-handoff-v1`

Canonical Hunter fields are:

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

Hunter signs a canonical body with freshness-bound HMAC headers. It has no HTTP/provider/browser/credential execution authority in this module.

Apply already validates the exact Hunter envelope, verifies HMAC/freshness/body digest, normalizes it into its local handoff ledger, enforces idempotency, and explicitly treats acceptance as **not** a provider action or submission.

Phase 12 extends this seam. It does not replace it.

## 4. Single-writer authority matrix

| Concept | Canonical writer | Secondary role | Classification | Required rule |
|---|---|---|---|---|
| Candidate/Profile truth | **Hunter** | Apply consumes a versioned projection/cache for application filling | Hunter: KEEP / Apply: NEEDS ADAPTER | Apply must not independently overwrite career truth without an explicit user-confirmed return contract |
| Master Resume evidence | **Hunter** | Apply consumes references/hashes only | KEEP IN HUNTER / SHARE THROUGH CONTRACT | Original extracted evidence remains immutable |
| JD-specific resume artifact | **Hunter** | Apply selects/uploads exact artifact and records the exact hash used | SHARE THROUGH CONTRACT | Apply never silently substitutes a different file |
| Candidate recurring answer defaults | **Hunter** | Apply maps defaults to page controls | SHARE THROUGH CONTRACT | Sensitive/protected answers remain provenance-aware and reviewable |
| Page-specific question semantics | **Apply** | Hunter may receive normalized unresolved requirements | KEEP IN APPLY / SHARE THROUGH CONTRACT | Browser-observed wording/control identity remains Apply-owned |
| Generated free-text answer review | **Apply** | Hunter supplies truth/evidence references | KEEP IN APPLY | Generated text cannot invent unsupported facts |
| Job/preparation identity | **Hunter** | Apply stores immutable linkage | KEEP IN HUNTER / SHARE THROUGH CONTRACT | `preparation_id` remains stable correlation input |
| Browser application identity | **Apply** | Hunter stores returned projection | KEEP IN APPLY / SHARE THROUGH CONTRACT | Apply owns runtime application/session correlation |
| ATS family/page identity | **Apply** | Hunter may perform coarse provider classification for preparation | KEEP IN APPLY / NEEDS ADAPTER | Browser-observed ATS identity wins over coarse URL classification |
| ATS account/credential operation | **Apply/native companion** | Hunter Phase 10 metadata slots are migration/foundation data only | Apply: KEEP / Hunter: DEPRECATE LATER | Secret plaintext never crosses the bridge |
| Candidate eligibility/work preference policy | **Hunter** | Apply consumes resolved policy inputs | KEEP IN HUNTER / SHARE THROUGH CONTRACT | Career targeting truth is not re-authored by browser runtime |
| AutoPilot runtime policy | **Apply** | Hunter may request preparation, never execution authority | KEEP IN APPLY | Final submit/security checkpoint policy remains Apply-owned |
| Browser session/checkpoint state | **Apply** | Hunter receives summarized status only | KEEP IN APPLY | CAPTCHA/MFA/OTP/manual checkpoint cannot be inferred away |
| Submission/execution receipt | **Apply** | Hunter consumes immutable receipt projection | SHARE THROUGH CONTRACT | Receipt must distinguish ready, attempted, submitted, confirmed, failed |
| Career CRM state | **Hunter** | Apply emits verified execution events | KEEP IN HUNTER / SHARE THROUGH CONTRACT | Hunter transitions from evidence, never from handoff acceptance alone |
| Gmail/recruiter evidence | **Hunter** | Apply may consume a normalized read-only projection later | KEEP IN HUNTER | Email observation cannot mutate browser execution truth |
| Orchestration | **n8n for current architecture** | Hunter/Apply provide bounded capabilities | KEEP CURRENTLY | n8n coordinates; it does not become canonical profile/application truth |

## 5. Phase 12 contracts

### 5.1 Candidate Profile Snapshot Contract — Hunter → Apply

Purpose: provide Apply an immutable point-in-time view of resolved candidate truth suitable for application filling without giving Apply independent profile authorship.

Required identity/provenance:

- `contract_version`
- `tenant_id`
- `user_id`
- `profile_id`
- `profile_revision`
- `source_extraction_id` or equivalent stable evidence revision
- `generated_at`
- `profile_digest`
- fact records with stable `fact_id`, category, value/reference, trust level, protected flag, and provenance

Apply should map these records into its existing `MasterProfile`/`ProfileFact` vocabulary where possible rather than introducing parallel fact categories.

Protected or sensitive facts must remain explicitly marked. Absence of a fact is not permission to infer it.

### 5.2 Resume Artifact Contract — Hunter → Apply

Purpose: bind an exact prepared resume artifact to an application.

Minimum fields:

- `artifact_id`
- `kind`
- `sha256`
- `mime_type`
- `size_bytes` when known
- `source_preparation_id`
- `profile_revision`
- `job_id`
- immutable locator/reference appropriate to the runtime boundary

Apply must verify the selected/uploaded file hash and record the exact hash in execution evidence.

### 5.3 Candidate Answer Defaults / Truth Policy Contract — Hunter → Apply

Purpose: provide recurring candidate truth and policy defaults while leaving real employer control mapping to Apply.

Each item should identify:

- stable answer/fact key
- semantic category/type
- value or protected value reference
- trust/provenance
- sensitivity/protected flag
- review requirement
- whether candidate confirmation is required before use

Apply remains responsible for matching the actual observed question/control, detecting ambiguity, and escalating unresolved or sensitive cases.

### 5.4 Application Execution Receipt Contract — Apply → Hunter

Purpose: allow Hunter CRM to advance only from verified Apply execution evidence.

Minimum identity:

- `contract_version`
- `event_id`
- `correlation_id`
- `tenant_id`
- `user_id`
- `handoff_id`
- `preparation_id`
- `application_id`
- Apply runtime application identity
- ATS family/provider
- `occurred_at`
- event type/state

Required evidence fields as applicable:

- page/application fingerprint
- exact resume artifact hash used
- unresolved count
- security/manual checkpoint status
- submit-control observation
- submit attempt result
- confirmation evidence reference
- failure/recovery reason

Only explicit Apply events equivalent to `APPLICATION_SUBMITTED`, followed when available by `APPLICATION_CONFIRMED` / `APPLICATION_COMPLETED`, may assert browser submission progress. A handoff receipt alone cannot do so.

### 5.5 CRM and Gmail evidence projection — Apply/Hunter reconciliation

Hunter owns the outward career CRM projection.

Recommended interpretation:

- Hunter prepared package → `PREPARED` / `READY_TO_APPLY` / `NEEDS_INPUT`
- Apply accepted package → `HANDOFF_ACCEPTED` only
- Apply reaches review boundary → `READY_FOR_REVIEW` or equivalent non-submitted CRM state
- Apply emits verified `APPLICATION_SUBMITTED` → Hunter may transition to submitted
- Apply emits `APPLICATION_CONFIRMED`/`APPLICATION_COMPLETED` → Hunter may attach confirmation evidence and strengthen submitted confidence
- Gmail/recruiter confirmation without Apply submission receipt → attach evidence and flag reconciliation; do not fabricate a browser submit event
- Gmail rejection/interview/assessment/offer → update downstream CRM outcome evidence without rewriting the original execution receipt

## 6. Canonical identifiers and correlation

The existing Phase 9 IDs remain foundational:

- `handoff_id`: immutable bridge/replay identity
- `preparation_id`: Hunter preparation identity
- `application_id`: Hunter-side application linkage carried into Apply
- Apply local handoff record ID: Apply persistence identity, not a replacement for `handoff_id`

Phase 12 should add stable profile/artifact revisions but must not rename or shadow the existing Phase 9 identifiers.

Every reverse Apply → Hunter event must carry enough correlation to resolve the original `handoff_id`, `preparation_id`, and `application_id` without fuzzy matching.

## 7. State transition contract

Forbidden transitions:

- `HANDOFF_ACCEPTED -> SUBMITTED` without Apply execution receipt
- Gmail evidence -> synthetic `APPLICATION_SUBMITTED`
- unsupported provider -> automatic submitted/complete
- `NEEDS_INPUT -> SUBMITTED` while unresolved required input remains
- security checkpoint -> bypassed/complete without explicit Apply evidence

Allowed high-level path:

`PREPARED -> READY_TO_APPLY | NEEDS_INPUT -> HANDOFF_ACCEPTED -> APPLY_RUNTIME_ACTIVE -> READY_FOR_REVIEW -> APPLICATION_SUBMITTED -> APPLICATION_CONFIRMED | APPLICATION_COMPLETED`

The exact runtime state machine remains Apply-owned. Hunter stores only the CRM projection necessary for career tracking.

## 8. Adapter and deprecation plan

### Keep and extend

- Hunter `app.apply_handoff` Phase 9 signed envelope
- Apply `CareerOSHandoffConsumer`
- Apply contract package semantic/application event vocabulary
- Hunter encrypted Candidate Truth Profile and immutable Master Resume evidence
- Apply browser/session/checkpoint execution machinery
- Current n8n authority until an explicit later migration

### Needs adapter

- Hunter Candidate Truth Profile → Apply `MasterProfile`/`ProfileFact` projection
- Hunter resume artifact records → Apply artifact/file verification model
- Hunter answer defaults → Apply semantic question resolver
- Apply event envelopes → Hunter CRM transition projector
- Apply ATS family/runtime identity → Hunter provider/application projection

### Deprecate later, after migration proof

- Any duplicate independently editable Apply profile truth that conflicts with Hunter canonical Candidate Truth Profile
- Hunter operational ATS credential ownership once Apply/native credential boundary is proven end-to-end
- Any duplicate application-state fields that can advance independently in both repositories

Deprecation requires migration tests and compatibility readers; no destructive removal belongs in Phase 12 pre-build.

## 9. Phase 12 implementation boundary

The first implementation tranche is deliberately inert:

1. pure contract dataclasses/schemas and validators;
2. canonical serialization/digest helpers;
3. profile snapshot projection helpers;
4. execution receipt validation/projector logic that cannot perform browser/network/provider actions;
5. compatibility tests against the existing Phase 9 field names and Apply event vocabulary;
6. CI gate proving no submission/network/browser/credential authority was introduced.

Explicitly out of scope:

- HTTP delivery
- provider API calls
- browser navigation/fill/submit
- ATS login
- credential retrieval/use
- Gmail send/reply
- n8n execution changes
- production deployment
- production state mutation

## 10. Acceptance criteria for Sections 2 + 3

Sections 2 and 3 are complete when:

- one canonical writer is documented for each overlapping concept;
- Phase 9 handoff remains backward compatible;
- Candidate Truth Profile can be represented as an immutable versioned projection without moving profile authorship into Apply;
- resume artifacts are hash-bound;
- candidate answer truth and browser question mapping have separate authority;
- Apply execution events can be validated without granting Hunter browser authority;
- no event other than verified Apply execution evidence can claim submission;
- no secret plaintext crosses repositories;
- the implementation primitives are pure/inert and covered by regression tests;
- CI passes on the isolated branch;
- no staging or production deployment occurs as part of this pre-build branch.
