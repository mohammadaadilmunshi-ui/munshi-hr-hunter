# Phase 10 — ATS credential orchestration

Status: DONE — verified local safety foundation.

`app.ats_credentials` is separate from the legacy singleton Gmail vault. It
derives owner identity from `current_owner`, keeps account metadata and secret
ciphertext in separate tables, and uses AES-GCM with canonical identity-bound
AAD. Public functions never return a secret and no login, account-creation,
browser, HTTP, Apply, n8n, or submission operation exists.

Provider policy is fail-closed: Greenhouse/Lever/Ashby are accountless-possible,
Workday is account-common, SmartRecruiters is variable, and unknown providers
are unsupported. All live action policy flags are false. Available means an
encrypted local slot exists; it is not proof of login and never authorizes a
submission.

The private integrity verifier is purpose-bound, bound to the runtime owner,
and limited to AVAILABLE accounts; it is not a public API or a password-view
capability. It strictly verifies algorithm/key versions and AES-GCM AAD.
Ciphertext, nonce, key, provider, account, tenant, user, and secret-kind
tampering fail closed. All mutations use revision CAS under an immediate SQLite
transaction. The deterministic transition graph rejects arbitrary replacement;
only enum reason codes enter the event ledger. BLOCKED atomically purges secret
slots and prevents further writes.

Verification: focused credential, Phase 9 handoff, tenant, preparation,
product-schema, and staging-fixture regressions — 44 passed; compile/static and
`git diff --check` passed; migration 025 is unique. A second Sol review cleared
the cryptographic boundary with no HIGH/CRITICAL issue. No credential values are
returned publicly or written to events/errors, and no login, browser, network,
n8n, Apply, or submission authority exists.
