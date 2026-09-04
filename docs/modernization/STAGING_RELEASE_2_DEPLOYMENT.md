# Staging Release 2 deployment record

Status: `STAGING_RELEASE_2=DEPLOYED_AND_VERIFIED`

- Deployed SHA: `5aee6ac98216545aeb2f0d181ca63d81b7f1c248`
- Source branch: `feat/autonomous-career-os-foundation`
- Workflow: Netcup Staging Deploy run `33907626194` (success)
- Previous staging SHA: `b0a45dfa529797800a4860f84ac6d545f6cc9c1e`
- Timestamp: 2026-09-04 UTC

The canonical wrapper created the WAL-safe staging backup and retained rollback
to the previous attached SHA. SSH verified the deployed branch/SHA, healthy
Hunter, migrations 024–026 plus legacy Gmail tables, SQLite `quick_check=ok`,
and protected staging/production HTTPS boundaries (401 unauthenticated).

Only staging Hunter was recreated. Staging n8n, Ollama, and edge identities
were unchanged; production container identities were unchanged. Production
impact: NONE. Existing staging fixtures were not reseeded.

Wave 2 local regression evidence: 57 passed. Phase 9 remains receipt-only;
HANDOFF_ACCEPTED is not Submitted. Phase 10 is security-cleared and retains no
login/submission authority. Phase 11 is local OAuth/evidence only;
`LIVE_GMAIL_OAUTH=BLOCKED_EXTERNAL`. n8n authority is PRESERVED and Prepare !=
Submitted is PRESERVED. Browser QA remains unavailable because the browser
runtime client is absent; authenticated access was not bypassed.
