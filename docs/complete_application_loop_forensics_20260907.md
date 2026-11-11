# Complete Application Loop V1: forensic baseline

Inspected 2026-09-07 before implementation changes. No deployment or runtime access.

## Verified source candidates

| Repository | Main | Existing integration HEAD |
| --- | --- | --- |
| Hunter | b64826bc433d1cd97ee4318624342d28751bef35 | 533028e3db51a4a97cd661906b35722ea4607ce4 |
| Apply | bcb6caf4e43eaa235cbd77cca468a3d72c58d7c9 | 73dbbb50282781a5332dfde4fe719707b0e5b961 |

Both integration branches are already named `feat/complete-application-loop-v1`; continue these with ordinary commits. Neither has a PR or Actions run at this baseline. Main is not an appropriate implementation base.

Hunter merge c30638c112b2cbd3d1cc1115f40a681a7e7b97a6 has parents 2d7654425d037ea200d607856d6db4a5f363a731 (Stage B) and f9edd53617b17a2e95b4ed72b5328e4ffb1ef1c6 (profile parity). Both are verified ancestors of integration HEAD. PR 33's description names an older commit: use Git objects, not that description, for locking.

Apply integration HEAD includes e955328bf4ebcafda591d57d3bb59ff4874d1eb6 (PR 14), 64a4762c569a696c1a723c9c6896765cad8b1e19 (resolution orchestration), and ccde77f999c34ac7e61ba3c4b6b97dcc3f8cb989 (V3 browser foundation), verified with `git merge-base --is-ancestor`.

## PR graph

Hunter PRs 24–26 strengthen Phases 4–7; 27 carries their staging workspace; 28–30 add truth projection, preflight, and Stage B; 31–33 retain the chooser, packaging fixes, model-free parsing, permanent profile, and draft-only rebuild. These PRs remain open (all draft except 31). PRs 16–23 are merged into the Native Resume lineage. Apply PRs 11–14 remain draft/open, with the resolution and handoff foundations in the ancestry. Do not close, merge, or replace historical PRs.

## Existing modules to preserve and reuse

- Hunter: profile truth overrides/projection/workspaces; Native Resume V4/V5; JD intelligence, requirement match, tailoring plans, claim trace; Answer Brain V2; preflight package; signed `apply_handoff`; tenant ownership; encrypted vault; engine selector; current product shell.
- Apply: content scanner, semantic engine, adaptive fill, navigation, application model, checkpoint store, ResolutionTask store/UI, signed handoff consumer, native messaging, current SQLite ledger.
- Existing preflight intentionally exposes an answer inventory without execution bodies. V2 plans are separate and must remain so.
- Native V5 materialization exists, but tests currently mock DOCX/PDF renderers. Actual artifact/page-count proof remains required.
- Hunter owns Candidate Truth and CRM. Apply's application/job records are execution mirrors, not canonical candidate truth.

## Diagnosed gaps and release blockers

1. Apply `CompleteApplicationLoopService` and V2 consumer have no production runtime callers. Browser adapter exists only as a Protocol, with no implementation or session tests.
2. Submit checks stored state; it does not independently rescan current browser state before acting. Adapter exceptions leave ambiguous execution recovery incomplete. Success recognition is overly permissive (any URL transition can count).
3. Session access is by identifiers without an explicit owner boundary; local job IDs need tenant-safe mirror identity. Resolution retrieval is application-wide rather than plan/session-scoped. Event sanitization is only top-level.
4. Review/receipt answers come from the original plan, omitting subsequently resolved answers. Receipt chain is captured before the final outcome event.
5. Hunter V2 plan capture is Native V5-only. n8n selector route is preserved, but n8n artifacts do not yet enter the execution plan.
6. Protected resolver references are accepted syntactically without proving resolvability/current truth. Caller-supplied sensitivity can override inferred protected classification.
7. Hunter durable authoritative lifecycle and receipt/mail ingestion are not implemented in these integration commits.
8. Existing integration commits are not covered by verified exact-HEAD CI; new Hunter tests are absent from the enumerated Native Resume workflow.

## Schema and testing baseline

Hunter additive migrations 034–040 cover preflight/Stage B; 041 registers Native V5 artifacts and Application Plan V2; 042 registers transport. Existing 041 duplicate was already removed. Apply 014 adds plans, sessions, reviews, commands, receipts and mail tables after 013 preparation handoff. Never renumber applied historical migrations; new corrections must be additive.

Existing tests: `test_phase9_application_preflight`, `test_application_plan_v2`, `test_application_plan_transport_v2`, `test_phase8_application_truth`, `test_phase45_truth_binding`, `test_stage_b_native_resume_v5`, tenant/vault/profile tests; Apply `test_application_plan_handoff_v2`, Candidate Truth contracts, checkpoint/resolution/native tests. New browser submission/invalidation/receipt integration proof is absent.

Local `npm run check` stops on Prettier failures in the two inherited `required-question-resolution` files. Classify as inherited formatting defect; format only those files after recording this baseline. Python dependencies require separate environments: Hunter pins pytest 9.1.1 while Apply dev requires pytest below 9. This is an environment packaging conflict, not justification to loosen either project.

High-risk flags remain off. Current V3 final-submit/security hard-blocks must stay intact for generic automation; any explicit submit capability must be separate and review-bound. No real credentials, email, applications, production DBs, staging, or deployments are involved.
