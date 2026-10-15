# LinkedIn Employer Careers Resolver — First-Party Evidence V3

**Status:** source-only, draft PR. Not connected to production, not deployed.

## Objective

Find the canonical employer careers site and exact ATS board for LinkedIn
jobs without trusting LinkedIn's fragile job_url_direct parser.

V1: conservative cross-source URL candidates.
V2: known healthy ATS board resolver, with official public posting verification.
V3: explicitly approved first-party employer-domain intake and public careers
page inspection, without guessing company domains or application URLs.

## New modules

- app/linkedin_first_party_careers_v3.py
  - ApprovedEmployerSite carries company, first-party root, careers URL, and
    audit approval provenance (not cryptographic authorization).
  - Rejects non-first-party, HTTP, IP literal, host spoofing, credentials, and
    invalid URLs.
  - Parses ATS board hints from an employer-owned static HTML snapshot.
  - Preserves employer job-page links separately from actual Apply URLs.
  - Reads HTTPS links in LinkedIn descriptions as UNVERIFIED hints only.
  - Supplies plain candidate search phrases to a separately authorized search
    service; no search or linked scraping performed by this module.
- app/linkedin_official_site_fetch_v3.py
  - One-off read-only public careers HTTPS GET, only for approved first-party
    company domains. Validates every DNS answer is public, connects to a pinned
    resolved IP, verifies TLS hostname and certificate, blocks HTTP redirects,
    refuses non-HTML, caps to 3 MB, and times out after 7 seconds.
  - No requests to LinkedIn, no arbitrary redirects, no credentials or POST.
  - Still requires signed operator/registry approval before production use:
    caller-provided provenance labels are not themselves authentication.
- scripts/linkedin_first_party_snapshot_v3.py
  - Offline JSON approval + HTML snapshot inspector; no network/database calls.
- tests/test_linkedin_first_party_careers_v3.py and
  tests/test_linkedin_official_site_fetch_v3.py
  - Host and URL safety, DNS/private-network rejection, hint-only status,
    exact first-party host, no redirect, pinned-host GET, and bounded content.

## Real first-party website probes, October 15, 2026

- Figma first-party page: https://www.figma.com/careers/
  Static public HTML ~1.83 MB, containing a Greenhouse board link. Official
  Greenhouse board token 'figma' identified. This step is a BOARD HINT, not an
  auto-apply target.
- A separate official Greenhouse public GET cross-check resolved a stored
  LinkedIn Figma HRIS Analyst job at San Francisco, CA to the matching official
  Greenhouse job URL. The job board lists multiple possible office locations;
  V3 fixes conservative multi-location parsing in the link matcher.
  Exact job identity corroborated with the public job detail description.
- Duolingo first-party page: https://careers.duolingo.com/
  Static HTML ~3 KB without extractable ATS links. Some careers pages render
  job links using JavaScript. A controlled renderer is still needed.
- The existing public ATS registry covers 26 of 2,638 LinkedIn records.
- Among 321 LinkedIn descriptions containing 'http', only 281 produced HTTPS
  hints after safety filtering, and zero produced recognized ATS hostname URLs.
  These are untrusted hints, not destination links.

## Explicitly not implemented yet

- Automated acquisition of authenticated/signed employer-domain provenance
  from a verified company website or trusted external provider.
- JavaScript renderer restricted to approved first-party corporate job pages.
- Durable application URL evidence schema, migrations, and idempotent backfill.
- Separate LinkedIn vs employer URL buttons and Prepare Application UI repair.
- Production integration with Tsenta or MUNSHI native application submission.
- Rate limiting, TTL cache, network egress policy, and integration monitoring.

## Guardrails and rollback

No production DB mutations, patches, deploys, applications, or LinkedIn
automated scraping. Only public employer/ATS GET requests were used for
specific approved-domain tests. All new changes are additive except the
conservative multi-location comparison fix. Rollback by reverting the draft
branch; no schema rollback necessary. Future release requires explicit
approval, CI/staging, exact SHA reconciliation with Netcup production.
