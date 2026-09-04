# Phase 10 — ATS credential orchestration

Status: PARTIAL local safety foundation.

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

Remaining before Phase 10 can be marked DONE: exhaustive tamper/AAD/key-version
tests, transition/revocation CAS coverage, and a final Sol re-review.
