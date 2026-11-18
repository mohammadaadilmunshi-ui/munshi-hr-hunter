# MUNSHI preparation before Codex + Tsenta OAuth

Date: October 15, 2026
Branch: feat/linkedin-employer-url-resolution-20261015 (PR #103, draft)

> Historical pre-implementation snapshot. The authenticated connector,
> provider router and observation foundation were subsequently implemented in
> source and tests. See
> `docs/MUNSHI_TSENTA_NATIVE_INTEGRATION_IMPLEMENTATION_20261015.md`. Nothing
> from that implementation was deployed or used to submit an application.

This increment adds two independent, **non-deployed, inert** components.

## 1. Bounded LinkedIn employer link enrichment worker (preview mode)

- `app/linkedin_batch_enrichment_v6.py`
- `scripts/linkedin_batch_enrichment_preview_v6.py`

The run reads stored LinkedIn jobs and already verified, healthy company ATS
board records. No LinkedIn scraping, browser Apply click, third-party login, or
applicant personal information is necessary. Public ATS reads are explicitly
opt-in via `--public-ats-get`; default is fully offline. Results are
unsigned `VERIFIED` *proposals* and cannot automatically enter the dashboard,
because MUNSHI's evidence writer requires an independent trusted signature.

Security and operational limits:
- 1–100 jobs / 1–20 boards, per-execution budgets
- Public GET only to known ATS hosts; no redirects with the default opener
- Bounded per-feed bytes and ~24MB in-process cache
- Reuse a board's response across jobs to avoid duplicate GETs
- Explicit zero SQLite writes, zero applications submitted
- No hidden background scheduler created by this PR

Example:

    python -m scripts.linkedin_batch_enrichment_preview_v6 \
      --db /path/to/read_only_copy/hunter.db \
      --max-jobs 50 \
      --max-boards 12 \
      --public-ats-get

The 50 newest live LinkedIn jobs were inspected using read-only SSH metadata
and a disposable SQLite in-memory copy: **50/50 had NO_VERIFIED_ATS_BOARD**.
This reveals inadequate company-board enrollment, not a parser or UI bug.

## 2. Tsenta account profile comparison and inert handoff

- `app/tsenta_profile_bridge_v1.py`

This module deliberately contains **no Tsenta credentials, guessed MCP
tool names, or network endpoints**.

After a user grants OAuth and an authorized tool can read Tsenta account
profile observations, the module can compare approved field values IN MEMORY
with already confirmed MUNSHI Candidate Truth. It returns only status labels
(MATCH, CONFLICT_REQUIRES_REVIEW, MISSING_TSENTA, etc.), not PII. No MUNSHI
profile changes occur automatically. Fields absent from the Tsenta capability
inventory must not be invented or implicitly imported.

The inert handoff envelope carries:
- Existing Hunter job ID and confirmed external employer URL
- Optional original LinkedIn URL for provenance
- Candidate Truth digest, resume digest and preparation package digest
- Idempotency digest
- Explicit `submit_authorized=false`; NO personal data or credentials

This design is provider-neutral and is not yet a functioning Tsenta adapter.
Tsenta requires user-authorized OAuth. After connecting, inspect the actual
MCP tool inventory and supported scopes, confirm whether profile read/export,
resume selection, application creation, and receipt retrieval tools exist, and
then write a thin adapter using only those real supported operations.

## What is still required to make the two URL buttons automatic in production

1. Establish a trusted employer-domain + ATS board enrollment source,
   covering substantially more than the small known board registry.
2. Authorized **server-side signed proof** and a verified evidence persistence
   worker. Do not treat unsigned public GET matches as certified persisted URLs.
3. Install the existing V5 migration in staging with backup/rollback and
   display the existing LinkedIn/Employer buttons with real persisted evidence.
4. Add a scheduler/job-ingestion event hook only after write authorization,
   failure retry/idempotency, rate limits and ownership checks pass.
5. Separately fix and verify Prepare Application; not a hyperlink concern.
6. Complete Tsenta OAuth capability audit and test real application handoff
   before automating submissions.

Until those gates pass, **do not merge PR #103 or deploy it in production**.
This is pre-Codex groundwork, not finished Tsenta account integration.

Privacy: Do not commit user profile exports, tokens, resumes, applications,
signed secrets or private signing keys. No private user profile data was
read from Tsenta during this implementation.

## Latest controlled live public ATS batch probe

Using 4 known, previously matched LinkedIn job records drawn from live
Hunter via read-only SSH and copied only into disposable in-memory SQLite:

- Four jobs scanned, four independently VERIFIED against published ATS feeds.
- Four employer boards queried, six public GET requests (two Greenhouse
  job-detail follow-ups); zero database writes, zero applications submitted.
- Proposals are clearly labelled UNSIGNED_PROPOSAL_ONLY, not dashboard-ready
  signed evidence. No TSENTA or applicant information was sent to those boards.
- On the separate 50 most recent LinkedIn jobs, **zero** had an enrolled
  verified board; therefore zero were eligible for automatic public ATS lookup.

All **249 focused tests** covering this branch's resolver, profile bridge,
dashboard link model, and legacy prepare-flow boundaries pass locally.
