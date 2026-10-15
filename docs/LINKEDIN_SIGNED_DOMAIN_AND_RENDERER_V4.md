# LinkedIn Employer Link Recovery V4: signed approvals and offline rendering

Status: read-only, source-only draft PR. Production routing unchanged.

## Achieved

- app/linkedin_signed_domain_approvals_v4.py:
  Ed25519 verifier with exact canonical signed attributes (employer, HTTPS
  corporate domain, careers URL, evidence URL, reviewer ID, UTC issue/expiry).
  At most 90 days validity; key rotation via externally supplied public
  keys and externally managed revocations. A signature attests to an operator
  approval, NOT independent proof of domain ownership. No signing private
  keys are stored in GitHub.
- app/linkedin_employer_domain_registry_v4.py:
  Read-only bounded JSON manifest loader and company-specific selection.
  Trust roots must be provisioned externally, never from the untrusted
  manifest. Ambiguous, missing, duplicate, stale, revoked, tampered or
  unknown-key records fail closed.
- app/linkedin_offline_careers_renderer_v4.py:
  Fresh Playwright Chromium, disabled by default; receives previously
  fetched HTML, with no applicant data, credentials, cookies, or existing
  user session. Rejects network requests, WebSockets where supported,
  popups, downloads, service workers, and browser dialogs.
  Bounded inline JavaScript can create ATS hints. External JS bundles are
  BLOCKED. Browser interception is not a complete sandbox; production
  requires a dedicated network-isolated process/container without access
  to internal, local, metadata, or private network resources.
- V3 resolver + V4 signed gateway:
  Signed approval -> restricted first-party HTTPS GET -> static board hint
  -> optional offline JS rendering -> public ATS feed/job detail GET
  -> identity corroboration -> VERIFIED employer URL. No submission authority.

## Live read-only demonstration (October 15, 2026)

An ephemeral test Ed25519 key generated in memory (not production-authorized)
signed Figma's public careers domain. The gateway successfully verified
the signature, used restricted HTTPS to read the official careers page,
found the Greenhouse board, and recovered the Figma HRIS Analyst URL:
https://boards.greenhouse.io/figma/jobs/6201385004?gh_jid=6201385004

Result: VERIFIED job link, submit_authorized=false, no application submitted.
The ephemeral key was never stored in GitHub or production.

Duolingo's careers page is a JavaScript shell with no static ATS link.
The offline browser will not load its external JavaScript bundles, so
that particular first-party fallback remains unresolved.

## Remaining gates

1. Enroll real site approvals through independently verified, audited
   ownership evidence, a privileged signing workflow, an authoritative
   public-key trust store and a revocation store. Operator test labels
   or public searches alone are not trusted domain approval.
2. Production-grade browser sandbox and bounded, approved script loading.
3. Persistent verified URL evidence table with safe dry-run-first backfill.
4. Idempotent dashboard separate LinkedIn and employer link handling.
5. Diagnose Prepare Application separately.
6. Exact-sha staging release, Linux/Docker CI, and approval before deployment.
7. Cache and rate-limit public ATS reads before any batch job.

## CI note

Earlier draft PR CI run 38804746112 failed an unrelated existing resume
artifact DOCX hash mismatch test. Do not suppress or weaken it. No resume
code was changed here, and the PR must remain draft until all required
checks pass or the unrelated failure is separately resolved.

## Rollback

Source additions are isolated and disabled by default. No production DB
schema changes, deployments, application submissions, or LinkedIn automation.
Revert the draft PR to remove the feature.
