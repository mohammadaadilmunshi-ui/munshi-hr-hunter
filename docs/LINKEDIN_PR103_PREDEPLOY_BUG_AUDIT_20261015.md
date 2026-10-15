# PR #103 pre-deployment bug and regression review

Date: October 15, 2026
Review outcome: HOLD production deployment pending gates below.
Live Netcup Hunter commit observed: b8cd45ea0390.

## Confirmed bug

V5 originally accepted a caller-supplied signed_approval_validated=true flag
without verifying cryptographic proof at the evidence-write boundary. This was
reproduced on an in-memory synthetic SQLite job fixture. It could allow
unsupported employer destinations to be stored as VERIFIED by a privileged
caller that supplies fabricated result data.

## Fix and regression evidence

- Introduced signed exact-job Ed25519 link evidence proof verification.
- Proof binds the exact job identity, source URL, employer URLs, ATS,
  provenance, verification timestamp and evidence digest.
- The evidence writer now rejects absent signatures, altered URLs/provenance,
  revoked approvals, unknown trust keys, tampered signatures, and expired proof.
- Focused tests: 222 passing after the patch. Ruff and compileall passed.
- Created a disposable staging SQLite database with the real production
  jobs table CREATE TABLE schema (retrieved read-only from Netcup).
- Controlled migration, unsigned rejection, signed insertion, source URL
  preservation, UI employer URL projection and legacy job immutability
  all passed. Disposable DB was discarded.
- Production DB was NOT migrated; no employer application was submitted.
- Before this patch, draft PR #103 had 12/12 green CI checks; new CI on the
  security-patch commit is required.

## Unresolved production release gates

1. Production trust roots MUST come from server-owned protected configuration,
   not caller-provided arbitrary verification keys. Enforce authorized signer
   identity, revocation, and audited operator approval.
2. A trusted signing authority must independently verify published employer
   job identity and official ATS/first-party origin before signing a link.
3. Controlled Netcup staging deployment, web UI smoke tests, migration backup
   and rollback readiness, and full green CI on this revision.
4. Separate Prepare Application digest/profile error investigation with a
   selected real job; no assumption the internal operation works.
5. Never promote on test flags alone. Refresh posting and authorization
   before actual MUNSHI or Tsenta submission.

This branch remains a DRAFT PR, not deployed. No production DB writes.
