# MUNSHI Employer Careers Resolver V2

Status: draft PR source-only; not wired into production dashboard or application submissions.

## Scope

Extend the V1 LinkedIn employer-link resolver using already-verified employer ATS boards.
Do not change existing LinkedIn scraping, scoring, targeting, scheduling, production
databases, migrations, or auto-apply execution.

New components:

- app/linkedin_employer_careers_v2.py
  - Reads healthy and enabled records from the existing ats_company_registry.
  - Checks board_token against the exact trusted ATS-hosted careers URL.
  - Extracts ATS board hints from caller-supplied official employer-careers HTML.
  - Never guesses employer domains, employer board IDs, or LinkedIn redirects.
  - Network requests require allow_network=True and only fixed ATS public GET endpoints.
  - Fails closed on timeouts and oversized responses.
- app/linkedin_public_ats_feed_v1.py
  - Greenhouse public list followed by bounded exact job-detail GET for matching titles
    and locations; prevents matching a similarly named but different job.
  - Provider-specific response limits (Greenhouse/Lever/SmartRecruiters 2 MB, Ashby
    16 MB due actual public board sizes). GET-only, no redirects, 7-second request timeout.
- scripts/linkedin_careers_registry_audit_v2.py
  - Read-only, offline coverage diagnostics for existing healthy company boards.
- tests/test_linkedin_employer_careers_v2.py
  - Health/identity guard, HTML hints, unexpected network prohibition, mocked public
    ATS feeds, actual Greenhouse detail validation, error recovery, dry-run SQL.

## Netcup production evidence (2026-10-15, no mutations)

Production Hunter anchor: b8cd45ea0390.
Total LinkedIn jobs: 2,638.
Exactly one known healthy enrolled ATS board: 26 LinkedIn jobs.
Unknown or untrusted board: 2,612 LinkedIn jobs.
Existing cross-source candidate links: approximately 97 (not verified).

Controlled live public ATS GET audit of a small sample yielded:
- 2 Duolingo Greenhouse matches confirmed by company, title, location,
  strong description agreement, and public ATS listing data.
- Other sampled jobs were unresolved, no match, or returned public API errors.
- Notion and Harvey Ashby board responses exceeded initial 2 MB safety cap.
  Follow-up inspection established approximate public payload sizes of 2.3 MB and
  4.4 MB; OpenAI board was about 13.4 MB. An Ashby-only 16 MB cap was introduced.
- Harvey also produced a network timeout; resolver now explicitly returns UNKNOWN
  with an error classification, never a guessed URL.

These 2 successful link resolutions DO NOT represent real job applications or
submission receipts. Official feed presence also does not guarantee a later
employer form will remain available.

## Gaps before main dashboard integration

1. Most LinkedIn companies have no vetted official company domain or enrolled ATS
   board. Resolve first-party corporate-careers identity through authorized search
   or manual employer-site verification; do not infer from names or third-party URLs.
2. Add a safe first-party site fetcher (DNS/IP validation, HTTPS, TLS, redirect policy,
   egress isolation) before remote HTML collection. Current HTML parser only accepts
   pre-verified first-party snapshots; it does not browse arbitrary company sites.
3. Implement immutable source/apply URL evidence storage, history and idempotent
   dry-run backfill. Preserve job identity and all prior applications.
4. Keep application submit gated on verified job identity, current active posting,
   explicit approved candidate facts, and submission authority.
5. Separately fix Prepare Application dashboard failures; source-only resolver code
   does not repair the Prepare operation or job-card UI.
6. Cache board responses by verified board with TTL before high-volume calls. No
   high-volume polling until throttle and HTTP error handling are tested.
7. Require staging and full GitHub CI checks, source SHA reconciliation, and manual
   production promotion; do not deploy from a draft PR.

## Rollback

Modules are additive; no production wiring is changed. To roll back, revert the
feature branch changes or leave modules unimported. No database restore required.
