# MUNSHI mail identity and signed employer-link recovery — October 20, 2026

## Verified identity roles

Employer ATS registration and verification reuse the single user-owned opaque
address u_bnicoocy3lmxwtl@mail.munshi.systems. New ATS credential records
reuse the owner identity and receive unique passwords, not new email addresses.
notifications@munshi.systems is the SMTP2GO outgoing sender;
aadil@munshi.systems is the SMTP2GO administrative account, not the ATS address.

Cloudflare MX records for munshi.systems and mail.munshi.systems still resolve
to Cloudflare. A controlled earlier test reached Hunter's mail_events_v2 inbox.
One owner identity is ACTIVE. Three existing ATS credential slots are Workday;
no actual iCIMS or Oracle account has been registered by this code release.
In the production mail database 18 Workday verification records are REJECTED
and 15 WAITING; an old waiting record is not proof of a live verification email.

The SMTP2GO key and sender decrypt. SMTP2GO recognizes the stored API key,
but rejects the read-only allowed_senders/view account endpoint because that
permission is not granted. This is not an email sending failure proof.
The notification worker remains disabled until an actual controlled send and
independent recipient confirmation succeed.

## Cross-source link recovery

Live 30-day preview (2026-10-20): 883 LinkedIn jobs and 43 cross-source
unverified employer-link leads: Workday 6, iCIMS 5, Oracle 4, Ashby 2,
Greenhouse 1, Paylocity 1, unknown/employer-site 24.

MUNSHI's bounded public ATS checker inspects only URLs under recognized ATS
hosts, by GET, without following redirects, authentication, POST, browser
interaction, or submission authority. Even a reachable page with a matching
title remains an unsigned lead. Unknown/custom employer sites are not sent
to a generic HTTP client; independently verified employer ownership is needed.

The automatic supervised read-only recovery lane already exists behind
MUNSHI_LINKEDIN_RECOVERY_AUTO_ENABLED=true. It is currently unset in Netcup.
Activating it is an operational step requiring an authorized container
configuration change or equivalent supervised timer. Checkpoints are kept
beside the mounted Hunter DB, and never change jobs.apply_url.

## Independently signed exact-job evidence importer

The new app.linkedin_signed_evidence_import_v1 CLI is dry-run by default
using SQLite mode=ro and PRAGMA query_only=ON. The input JSON must contain
schema=munshi-external-signed-link-import-v1 and an items array of 1 to 100
entries with job_id, resolution, verified_at, and signed_proof.

Trusted Ed25519 public keys are loaded only from an independently maintained
operator file, never from the proposal bundle. The independent signer owns
the private signing key outside Hunter. Hunter cannot sign candidate leads.
It verifies the signed full content hash, exact persisted job, URL, provider,
official-source provenance, proof expiration, and revocation. Commit needs
--commit --acknowledge-signed-import and
MUNSHI_SIGNED_LINK_IMPORT_ENABLED=true plus the already-installed V5 table.
The whole batch is transactional, idempotent, and append-only; it does not
authorize submission, change a job's original LinkedIn URL, or write
application state.

Provisioning the external signing authority remains required before verified
proposals can be imported and exposed to the application buttons. In
particular, the 43 cross-source leads cannot be automatically certified
without sufficient independent evidence. No signer private key is embedded
in the repository or production container.

## Deployment constraints

The Netcup code-only gateway must be repinned from its previous live Hunter
baseline to the exact currently deployed SHA, preserving the trusted
GitHub CI exact-source checks, narrow file allowlist, rollback, and health
gates. The code-only release must not copy or migrate the existing 18 GB
database. No submission worker or notification worker is enabled by merging
code alone.
