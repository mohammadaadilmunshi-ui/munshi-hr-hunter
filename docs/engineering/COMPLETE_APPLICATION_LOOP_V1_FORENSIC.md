# Complete Application Loop V1 — Forensic Baseline

Date: 2026-09-06

## Scope

Source-engineering-only consolidation for Candidate Truth -> Job -> Prepare -> n8n or Native Resume -> Application Plan -> MUNSHI Apply -> ATS preparation -> Needs Input -> Final Review -> explicit user Submit -> verified receipt -> Hunter CRM state.

No staging or production deployment is authorized by this branch.

## Verified Hunter ancestry

- `feat/stage-b-jd-intelligence-v1` HEAD: `2d7654425d037ea200d607856d6db4a5f363a731`
- `fix/profile-parity-vault-activation-v1` HEAD: `f9edd53617b17a2e95b4ed72b5328e4ffb1ef1c6`
- Merge base: `f69bc0c0aa5a344a0b9029d38265aaaf224b66cf`
- The two heads are intentionally divergent: Stage B is 42 commits ahead of the merge base and the profile/vault line is 28 commits ahead.

Stage B carries Phase 9 preflight, JD Intelligence, Candidate/JD evidence matching, resume tailoring/claim tracing, Opportunity Intelligence V4, and Native Resume V5 foundations. The profile/vault line carries deterministic profile parity repairs, product UI/session-state repairs, and production-vault activation source/helpers. Neither head alone contains the complete desired source foundation.

## Verified merge reconciliation

The advanced Stage B tree is the source base. Profile/vault changes are overlaid where they are additive or strictly newer. The only material overlapping deployment file is `deploy/netcup/deploy_staging_release.sh`; the profile/vault version is retained because it includes the fail-closed rollback/status fixes while preserving Stage B's staging safety contract. The Dockerfile differences are ordering-only for existing copied validation scripts; the profile/vault version is retained.

No live deployment action, production DB mutation, n8n mutation, secret access, or external application action is performed by this consolidation.

## Verified Apply ancestry (cross-repository dependency)

- Candidate Truth consumer head: `e955328bf4ebcafda591d57d3bb59ff4874d1eb6`
- Resolution/browser-usability head: `64a4762c569a696c1a723c9c6896765cad8b1e19`
- Their merge base is Apply V3 foundation `ccde77f999c34ac7e61ba3c4b6b97dcc3f8cb989`.
- The known collision is migration number `012`: Candidate Truth/handoff uses `012_career_os_preparation_handoffs.sql`, while the resolution line uses `012_resolution_tasks.sql`. The integration branch must preserve both and renumber the preparation-handoff migration additively.

## Current authority boundaries

- Hunter remains the canonical Candidate Truth, job/preparation, application lifecycle, and CRM authority.
- Apply remains a consumer of a read-only Candidate Truth projection and owns browser-observed/execution state only.
- Existing Phase 9 `ApplicationPreflightPackage` is readiness intelligence and intentionally has no submission authority.
- The existing signed handoff is acceptance-only; handoff acceptance is not submission.
- n8n remains a preserved resume fallback. Native V5 may only be selected behind its explicit default-off feature gate.

## Release invariants

`PREPARED != SUBMITTED`

`READY_TO_APPLY != SUBMITTED`

`HANDOFF_ACCEPTED != SUBMITTED`

`PLAN_ACCEPTED != SUBMITTED`

Final submit must remain disabled by default and later require an explicit user command plus independent browser/ATS verification.

## Next implementation seam

After consolidation is test-green, add a separate immutable `ApplicationPlan` V2 execution contract in Hunter rather than weakening `ApplicationPreflightPackage`, then build signed transport acceptance, Apply execution sessions/checkpoints, structured resolution tasks, exact final-review approval, default-off submit authority, immutable execution receipts, and secondary mail-confirmation contracts.
