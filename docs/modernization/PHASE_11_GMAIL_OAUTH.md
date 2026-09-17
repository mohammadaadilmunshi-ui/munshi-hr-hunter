# Phase 11 — Gmail OAuth and Email Intelligence Foundation

Status: DONE — local foundation; `LIVE_GMAIL_OAUTH=BLOCKED_EXTERNAL`.

The legacy singleton Gmail integration and `secure_vault` remain unchanged for
compatibility and are not used by this tenant-safe boundary. Migration 026 adds
tenant/user-owned OAuth account metadata, write-only AES-GCM token slots,
hashed one-use authorization state with encrypted PKCE verifier, reason-code
events, and deduplicated observational email evidence.

Only the Gmail readonly scope is accepted. Token and authorization records use
identity-bound authenticated encryption, strict algorithm/key versions, CAS
state transitions, rotation, and atomic revocation purge. Private integrity
verification is owner- and purpose-bound; public status exposes no token,
ciphertext, nonce, OAuth state, verifier, or OTP value.

The local evidence model records conservative submission-confirmation,
assessment, interview, rejection, recruiter-response, and OTP/verification
signals with source identity, provenance, confidence, and deduplication. It
never changes an application lifecycle: confirmation evidence is not
`SUBMITTED`.

There is no Google HTTP client, authorization URL/callback exposure, email
send/reply, n8n, Apply, browser, or submission authority. Live OAuth requires
separately configured credentials and explicit authorization, so it remains
blocked externally. Sol reviewed the architecture before implementation.

Verification: Phase 11, legacy Gmail, Phase 10, Phase 9, tenant, preparation,
schema, and staging-fixture tests — 57 passed; compile/static and diff checks
passed. Legacy and migration-026 schemas coexist on fresh initialization.
