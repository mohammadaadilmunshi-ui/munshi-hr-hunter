# MUNSHI Complete Application Loop — Authoritative Continuation Handoff

**Updated:** 2026-09-07
**Current product roadmap:** V2.1 asynchronous application operating system
**Authoritative lineage:** Complete Loop worktrees on `feat/complete-loop-runtime-ux-20260907`

## 1. Safety boundary

Still mandatory:

```text
NO staging.
NO production.
NO merge to main.
NO live credentials.
NO real ATS submission.
NO real employer application.
NO real email/Gmail/OAuth.
NO live n8n workflow modification.
NO production DB/volume mutation.
MUNSHI_FINAL_SUBMIT_ENABLED=false.
```

Preparation evidence is never submission authority:

```text
PREPARED != SUBMITTED
READY_TO_APPLY != SUBMITTED
PLAN_ACCEPTED != SUBMITTED
BUTTON_CLICKED != VERIFIED
EMAIL_RECEIVED != PRIMARY_SUBMISSION_PROOF
```

## 2. Authoritative worktrees and heads

```text
Hunter:
~/PROJECTS/munshi-hr-hunter/.complete-loop-worktrees/munshi-hr-hunter

Apply:
~/PROJECTS/munshi-apply/.complete-loop-worktrees/munshi-apply

Branch in both:
feat/complete-loop-runtime-ux-20260907
```

Current source head before the docs-only handoff commit:

```text
Hunter source tranche:
c3726ed3b2d2bf99bb549e8419cac8d9aaed8c26
fix: bind n8n artifacts to exact preflight callbacks

Apply:
5f647932fcef6e6f679c5ca709060ad5e4ab0abe
feat: expose authenticated complete-loop commands
```

The handoff update itself is committed as a docs-only child of `c3726ed`. Because a Git commit cannot contain its own final SHA, the exact post-handoff Hunter HEAD is recorded in the external Phase 0 final manifest / takeover response and can always be verified with `git rev-parse HEAD`.

The earlier GitHub WIP lineage remains reference-only:

```text
Hunter WIP: ccf8a5574c937d4423ca7480d99ac25c8d612e7a
Apply WIP:  cf4b5f76b6eadca2fe4a24cbccdf7210c233320b
branch: wip/complete-application-loop-handoff-20260907
```

Do not merge the WIP branch wholesale. Its browser/runtime spike files may be reviewed/cherry-picked later.

## 3. Migration 044 — COMPLETE AND COMMITTED

Migration:

```text
migrations/044_n8n_preflight_callback_contract.py
```

Migration 044 adds the fail-closed n8n preflight callback binding to the existing queue and callback receipt structures.

Queue binding fields:

```text
tenant_id
user_id
preflight_package_id
preflight_package_digest
application_id
expected_job_snapshot_sha256
expected_candidate_truth_json
```

Callback receipt fields:

```text
payload_sha256
execution_artifact_id
```

The migration was tested against a legacy queue schema, applied twice, and verified with:

```text
PRAGMA foreign_key_check = clean
PRAGMA quick_check = ok
```

No existing migration number was rewritten.

## 4. Exact n8n dispatch/preflight contract now implemented

`insert_queue_item(..., preflight_package_id=...)` is the only artifact-capable n8n queue path.

When a preflight ID is supplied, queue creation requires:

```text
exact owner
exact READY_TO_APPLY package
exact job
exact application
exact package digest
exact Candidate Truth binding
exact job snapshot SHA
```

No package is inferred from:

```text
latest preflight
application_id alone
job_id alone
```

The dispatch payload echoes:

```text
preflight_package_id
application_id
```

alongside the existing queue/request identities.

The queue's artifact-capable idempotency material includes owner, preflight, package digest, application, dispatch mode, and webhook mode.

## 5. Exact callback contract now implemented

Completed application-artifact callbacks require:

```text
queue_id
request_id
preflight_package_id
application_id
```

Callback validation resolves authority only from stored Hunter queue/preflight state.

It verifies:

```text
exact queue_id + request_id
queue was actually dispatched/accepted/completed
exact queue job + fingerprint
exact queue application
exact queue preflight
exact package owner
exact package job
exact package application
exact package digest
READY_TO_APPLY package status
snapshot status/application/job binding
recomputed snapshot package digest
exact Candidate Truth binding
exact callback Candidate Truth binding
exact callback job snapshot binding
current owned job snapshot under the callback write lock
```

Artifact persistence remains:

```text
engine=n8n
submission_authority=false
application/pdf
exact lowercase SHA-256
hunter-n8n:// reference
page_count=1
positive immutable version_number
```

The callback never transitions the Complete Application Loop lifecycle to `SUBMITTED`.

## 6. Security review findings fixed in this tranche

This continuation performed the final GPT-5.6 Sol security review over the exact dirty diff and found material issues that the prior 25-test state did not catch.

### Finding A — freshness check could deadlock under `BEGIN IMMEDIATE`

The prior dirty implementation called `preflight_package_freshness()` after acquiring the callback write lock.

That freshness path opens additional SQLite connections and Candidate Truth vault reads update `last_used_at`. A direct diagnostic reproduced:

```text
sqlite3.OperationalError: database is locked
```

Fix:

```text
full owner-scoped preflight freshness
→ run immediately before callback write lock
→ capture PRAGMA data_version
→ BEGIN IMMEDIATE
→ verify data_version did not change
→ revalidate exact queue/package/job binding under lock
→ recompute current owned job snapshot under same connection/owner
```

This preserves freshness without opening writer connections underneath the callback lock.

### Finding B — successful exact callback retries could become non-idempotent later

Previously an exact retry re-ran mutable preflight freshness before checking the immutable callback receipt.

Therefore a callback that had already committed successfully could later fail simply because Candidate Truth changed after the original success.

Fix:

```text
exact callback identity + same payload + existing receipt
→ acknowledge duplicate immediately
→ return original execution_artifact_id
→ do not replay side effects
```

Same callback identity with changed payload remains:

```text
HTTP 409 CONFLICT
```

### Finding C — `executescript()` broke receipt/artifact transaction atomicity

`universal_n8n_progress.ensure_schema()` was invoked inside the callback transaction and used `sqlite3.executescript()`.

`executescript()` implicitly commits an open SQLite transaction. That meant a later callback failure could theoretically leave receipt/artifact/result state partially committed.

Fix:

```text
app/universal_n8n_progress.py
app/n8n_dispatch.py
```

now initialize schema using individual DDL statements that preserve the caller-owned transaction.

A forced late-failure regression proves rollback removes:

```text
callback receipt
execution artifact
n8n result
callback event
```

and leaves the queue at its previously committed `accepted` state.

### Finding D — stored preflight snapshot digest was compared but not recomputed

The callback now recomputes the stored snapshot digest and checks its READY/application/job identity before accepting artifact evidence.

### Finding E — explicit owner helper was partial/fail-open on malformed explicit owner arguments

`safe_owned_job_snapshot()` now:

```text
requires both tenant_id and user_id or neither
verifies explicit tenant membership
reuses caller connection safely
never closes caller-owned connection
```

### Finding F — Candidate Truth queue binding now fails closed if required binding keys are absent

Required:

```text
source_extraction_id
profile_revision
profile_digest
```

## 7. Callback replay / atomicity behavior

Same callback identity + same canonical payload:

```text
200-equivalent success response
duplicate_callback=true
same execution_artifact_id
no side effects replayed
```

Same identity + changed canonical payload:

```text
409 CONFLICT
```

Concurrent callback receipt claim remains protected by:

```text
BEGIN IMMEDIATE
PRIMARY KEY receipt_key
payload SHA-256
```

Receipt claim, artifact persistence, result updates, queue/progress updates, callback event, and job callback state are one deliberate SQLite transaction.

## 8. Execution-artifact persistence

Shared contract remains in:

```text
app/application_execution_artifact.py
```

Native path:

```text
Native V5 verified immutable PDF
→ shared execution artifact
```

n8n path:

```text
exact preflight-bound completed callback
→ validated n8n immutable PDF evidence
→ persist_verified_n8n(...)
→ shared execution artifact
```

Always:

```text
submission_authority=false
```

An application identity cannot silently rebind to different artifact payload/provenance.

## 9. Final verification at Hunter `c3726ed`

Combined focused/adjacent regression suite:

```text
54 passed
```

Files covered:

```text
tests/test_downstream_contract.py
tests/test_application_execution_artifact.py
tests/test_application_plan_v2.py
tests/test_phase9_application_preflight.py
tests/test_phase67_intelligence.py
tests/test_tenant_foundation.py
tests/test_product_schema.py
tests/test_staging_migration_validation.py
```

Additional checks:

```text
python -m compileall -q app tests migrations   PASS
git diff --check                              PASS
```

Ruff was not installed in the takeover execution environment:

```text
python -m ruff -> No module named ruff
```

This is an environment/tooling absence, not a source failure. Run the repository/CI lint gate from the established Hunter environment when available; do not weaken lint rules.

## 10. Permanent n8n scope decision

Under the V2.1 product roadmap, n8n remains an optional resume-generation engine only.

Allowed:

```text
Candidate Truth + JD
→ n8n resume pipeline
→ quality gates
→ immutable verified resume artifact
→ Hunter
```

Forbidden n8n authority:

```text
browser execution
application execution
final review
final-submit authority
submission verification
receipt authority
CRM authority
employer-mail authority
```

Native and n8n converge at:

```text
IMMUTABLE RESUME ARTIFACT
→ Application Package
→ MUNSHI Apply
```

No further Complete Loop execution logic should be added to n8n.

## 11. GitHub synchronization status

At the time of this handoff update, the authoritative worktree branch had not yet been pushed to GitHub.

The exact source ancestry that must be preserved includes:

```text
Hunter source c3726ed3b2d2bf99bb549e8419cac8d9aaed8c26
  parent 116f03362fb3d85f44c4a5314696b15d5d8cea1a

Apply 5f647932fcef6e6f679c5ca709060ad5e4ab0abe
```

The docs-only handoff commit is a direct child of `c3726ed`; use the external final manifest for its exact SHA.

Do not manufacture a replacement branch from the older GitHub WIP lineage. Push these exact local branches/objects.

## 12. Next product tranche after branch synchronization

Do not start signed transport next merely because V1 sequencing said so.

The V2.1 sequence is now:

```text
1. Push authoritative Hunter + Apply feature branches.
2. Keep n8n frozen at immutable-resume-artifact boundary.
3. Define provider-agnostic ProviderExecutor interface.
4. Build a controlled local Greenhouse-like application fixture.
5. Prove one complete application loop locally:

Candidate Truth
→ Job
→ Resume
→ Cover Letter
→ Answers
→ Review
→ Explicit Approval
→ Async queue
→ Server-side browser execution
→ exact upload/readback
→ exactly one synthetic Submit
→ provider-correlated verification
→ immutable receipt
→ Submission Record

6. Reuse/review WIP plan-runtime browser-brain code.
7. Relocate review permanently before execution.
8. Build async worker/state machine.
9. Add cover letters and Submission Record.
10. Build Applications / Needs You / Review UX.
11. Add synthetic owned-inbox interfaces.
12. Harden provider-specific browser adapters.
13. Harden concurrency/submit proof.
14. Complete signed Hunter↔Apply transport.
15. Cross-repository acceptance + CI.
```

No real ATS is required for the first complete loop.

## 13. Provider API decision

Do not assume public Greenhouse/Lever/Ashby job-board access grants arbitrary candidate submission authority.

Architecture:

```text
ProviderExecutor
├── AuthorizedApiExecutor
│   only when legitimate employer/provider credentials or partnership exists
└── BrowserExecutor
    universal external-employer path
```

For arbitrary external employers, browser execution is the primary path.

## 14. One-line takeover

```text
Migration 044 and exact n8n preflight-bound artifact convergence are committed at Hunter source commit c3726ed and the handoff is committed immediately after it; preserve that lineage, push both authoritative feature branches, then stop expanding n8n and build the first full asynchronous application loop against a controlled local Greenhouse-like browser fixture.
```

## 15. SOURCE_COMPLETE rule

Do not emit `SOURCE_COMPLETE` and do not create a source-complete handoff until the entire V2.1 source/test/CI definition is actually complete.
