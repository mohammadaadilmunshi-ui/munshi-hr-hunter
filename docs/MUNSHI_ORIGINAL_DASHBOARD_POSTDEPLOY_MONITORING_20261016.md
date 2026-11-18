# MUNSHI original dashboard: post-deployment checks and ongoing learning

Target: existing https://dashboard.munshi.systems, not a replacement staging website.
Date: October 16, 2026. Source changes do not authorize a live deploy.

## Local verification

Hunter full suite passed 1,578 tests with 3 nonfatal deprecation warnings after
using the repository's declared Python jobspy dependency, installing the exact
Playwright Chromium binary in the isolated test machine, and resolving
incompatible NumPy/PyArrow lock pins (NumPy 1.26.3, PyArrow 19.0.1).
Apply v0.2.12 passed 653 unit tests and the full formatter, linter, typecheck,
extension build, artifact verification, and secret scan.

Updated the original dashboard application view to fail gracefully when the
jobs table is unavailable. Fixed the Native replay score to demand matching
observed field properties, not just a correct SHA-256 field fingerprint.

## Read-only postdeployment API smoke

Command to run INSIDE the promoted Hunter runtime after the exact release:
python -m deploy.ops.verify_original_dashboard_learning

The smoke reads only the 127.0.0.1:8000 FastAPI loopback. It never redirects,
posts, uploads a resume, accepts OAuth consent, retries or submits applications.
It checks health, absent-key 401 protection, the status-only bridge isolation,
and, if HUNTER_API_SECRET is in protected runtime environment, authenticated
Tsenta connection metadata and privacy-minimized 10-15 cohort progress.
Run --strict-connected-tsenta only after a new Netcup tenant grant has been
explicitly authorized and a real read-only profile verification passes.
Only when the distinct bridge secret is also configured may the script check
that an intentionally unknown exact application returns 404, never a receipt.
Never print tokens, account identifiers or applicant records.

## Continuous learning after explicit owner authorization

By default MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=false, including
Hunter Docker Compose. It must remain false until protected backend OAuth
consent, valid profile read and schema integrity checks have succeeded.

Then an operator may configure a non-overlapping daily scheduler to run:
python -m app.tsenta_learning_monitor_v1 --once --pages 5

This code is source-ready and tested but NO TIMER WAS INSTALLED OR ENABLED.
It reads only 15 existing Tsenta applications per page (up to 5 pages by
default), existing statuses and available review-field structure, with no
apply/approve/retry/submit tool calls. Only hashed application/field references,
bounded field metadata and status signals enter the isolated owner database.
Repeated provider records cannot increase distinct processed counts.

10 terminal distinct processed examples begin a shadow evaluation cohort;
15 complete that cohort. Field counts and provider-reported submitted are not
employer receipts. The next 15 distinct examples comprise the next cohort.
Native mapping proposals require independent held-out replays on at least 5
additional applications and a 10-percentage-point gain without ATS regressions,
false positives or untruthful candidate answers. Promotion is a versioned,
reviewed NON-SENSITIVE field-priority pilot only, never self-editing production
answers, immigration/authorization facts, resumes, legal attestations, or
submission authority.

## Remaining release gates and monitoring

The original Hunter PR #104, stacked PR #103, and production-aligned Apply
v0.2.12 PR #64 must be promoted with approved exact SHA, preserved production
Compose overlays, validated DB backup/migration 058/059 and tested rollback.
The original dashboard HTTPS Caddy must route only the necessary OAuth
callback and GET-only status bridge without opening admin APIs. Cloudflare
Apply owner workspace gets a separate status-only credential, never the
general Hunter secret or a Tsenta token. Unsafe previously stopped staging
workers must be recreated with irreversible actions disabled before E2E tests.

Real multistage employer workflows must be tested on representative ATSs,
including Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Oracle, iCIMS,
SAP SuccessFactors, Dayforce and an unfamiliar ATS. Verify accurate candidate
answers, correct tailored PDF, protected checkpoints, actual READY_FOR_REVIEW,
submission authorization and independently confirmed employer receipts
separately. Static fixture tests do not prove any actual employer submission.

When errors appear, report unavailable or needs-review rather than fabricating
success. Capture recurring required-field failures per ATS, monitoring worker
health, learning evidence age, false-positive rate, actual independent
receipt rate and rollback status. Neither perfect performance nor automatic
rule promotion is guaranteed by 10-15 examples alone.
