# MUNSHI Native — provider learning and adapter promotion (2026-10-19)

This note is a development contract, not a claim of full ATS support.

## Sources and authority

- Public vendor claim inventory: https://tsenta.com/ai-disclosure, snapshot on 2026-10-19. Its 44 named ATS/job systems are **marketing claims by a separate provider**, not Native implementations.
- Authorized connected-account evidence: `tsenta_observations_v1`, owner-scoped, privacy-minimized, read-only MCP list/status/review tools. Never pull browser internals, credentials, raw resumes, question answer values, or data belonging to other users.
- Native validation evidence: separate synthetic and held-out real-browser tests. No provider row becomes executable by appearing in the vendor list or by exceeding a status count.

## Historical inventory and continuous synchronization

1. The sync client pages over existing authorized account applications, 15 per page, 20 pages maximum **per bounded cycle**, with an independently verified 200-page defensive history ceiling. It retrieves status and an available review; only sanitized projections persist.
2. Run with the dedicated read-only flag scoped to one process and a single server-side flock. The daily timer refreshes page 1 before continuing older pages from a durable, non-secret cursor; the newest applications are not starved by historical backfill.
3. `history_window_complete=true` only if a short provider page is observed. A full 20-page window persists its next page atomically; the next daily run continues where it stopped. The global cap is page 200 and must surface an explicit error if reached without an end-of-history marker. Provider page 21 was independently probed as supported on 2026-10-19; higher pages remain provider-behavior-dependent.
4. Transient SQLite BUSY/LOCKED writes retry at most three times after the initial attempt with fresh connections and bounded waits, reusing stable observation IDs. Other SQL failures abort without retry, and no provider submission is ever retried.
5. Incremental application/status observations are deduplicated by owner, opaque application digest, event kind, ATS, status/field fingerprint. Do not promote inferred or unavailable entries.
6. Dashboard/API `native_provider_learning_digest_v1` categorizes each public claimed platform, aliases, and any other observed ATS. No employer-specific hardcoded submission logic.

## Invariants

- Never guess credentials, applicant identity, sponsorship, legal disclosures, protected/self-ID answers or recruitment history. User-confirmed Candidate Truth and scoped secure policies remain authoritative.
- Account creation must run in an authorized Native browser with managed per-account credentials and a verified mailbox identity. CAPTCHA and employer-enforced human-only MFA remain user checkpoints.
- Unknown normal question -> pause with exact question/options and safe classification -> user provides an answer -> record once in Answer Brain under appropriate scope -> signed replacement Application Plan -> resume the **same** browser checkpoint. Repeat until resolved.
- Final employer Submit is one-shot. An uncertain result -> independent receipt/status reconciliation; do not submit again without evidence the prior attempt did not go through.
- Separate record types: vendor **claim**, provider status, browser-form observation, Native predicted control, held-out Native replay, independent employer receipt. Do not treat these as equivalent.

## How Native should learn from each provider

1. Collect distinct sanitized field fingerprints and classifications from authorized paused reviews; preserve control type, required, editable, option count, and value presence. Do not store submitted values.
2. Capture equivalent Native synthetic browser trace for the same field type (and where authorized, an isolated real-job non-submitting canary). Keep form version, locale, mandatory uploads, login/account steps, verification requirement, and checkpoint state separately.
3. Compare observations against Native predictions using independent samples, never training on the same application as the held-out replay. Require no ATS regressions and measurable field-recognition gain; only safe non-sensitive field-priority rules may be proposed.
4. Promote additions through guarded CI + manual code review + staging browser canary + exact-image Netcup release. Keep all promotions reversible and versioned. Never auto-install adapter code from observed form text.
5. Expand the Native browser using **general primitives**, not company-specific selectors: page/account state classifier, accessible control observation, employer-domain trust check, semantic-to-Candidate-Truth binding, document-upload verification, immutable checkpointing, mail artifact matcher, one-use submit claim, and independent receipt verification.

## Priority of next synthetic canaries

- **Form evidence first:** BrassRing, Dayforce, SAP SuccessFactors. These have observed sanitized form controls; establish independent Native field recognition replays.
- **High application-status exposure:** Workday, Indeed (additional observed platform), SmartRecruiters, Oracle Cloud, Paylocity, Greenhouse, and the other publicly claimed platforms with status-only evidence. Build page/form samples before claiming autonomy.
- **Low/no evidence:** All public claims without observed form controls. Identify legitimate demonstration pages and run isolated no-submission browser canaries; do not assume the vendor supports them in MUNSHI.
- **Open-world:** Preserve unknown labels and unmapped ATS, but never automatically classify an unknown website as a supported system.

## Release/testing gate

- Unit tests: all 44 claims included, novel ATS preserved, vendor claims never executable, no private values emitted, tenant-scoped DB read-only, existing-schema ingestion avoids unnecessary DDL.
- Operator checks: learning sync authorized and read-only; status reports 0 provider submissions and 0 Native auto-upgrades; database/schema/worker identity unchanged except sanitized learning insertions.
- Runtime: daily bounded scheduled synchronization with lock, timeout, journal receipt; do not overlap migrations or one another.
- A **real** Native account/signup -> verification email -> same-session multi-page continuation -> one-shot submit -> independent employer receipt canary remains outstanding. Current stored status observations do not constitute this proof.

## Deployment constraints

MUNSHI production Netcup only. Mac is an editing/control client, not a local MUNSHI runtime. Use the safe tested Hunter image-only release with pinned SHA, immutable current database volume, paused/idle submission gates, rollback and postrelease verification. Never use the old full-database migration workflow for a source-only update. The read-only learning timer must be installed by an authorized Netcup administrator and inspected after first run. If CI or live preflight fails, leave the existing dashboard untouched.
