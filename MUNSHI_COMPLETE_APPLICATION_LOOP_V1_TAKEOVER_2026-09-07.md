# Complete Application Loop V1 — continuation handoff

## Safe checkouts

- Hunter worktree: `~/PROJECTS/munshi-hr-hunter/.complete-loop-worktrees/munshi-hr-hunter`
- Apply worktree: `~/PROJECTS/munshi-apply/.complete-loop-worktrees/munshi-apply`
- Both branches: `feat/complete-loop-runtime-ux-20260907`
- Hunter base: `ccf8a5574c937d4423ca7480d99ac25c8d612e7a`
- Apply base: `cf4b5f76b6eadca2fe4a24cbccdf7210c233320b`

Never alter the original sibling working trees; they have unrelated local edits.

## Completed commits

Hunter:

- `0cf90c4` fixture schema setup for the artifact rejection assertion.
- `64b9ac0` actual DOCX OOXML validation plus independent PDF parsing and one-page enforcement.
- `0041b60` initial shared execution-artifact normalization contract.

Apply:

- `f07ba36` Ruff/format baseline repair; runtime build generation wired and generated output ignored.

## Verified gates

- Hunter: `tests/test_native_resume_artifact_v5.py` and `tests/test_application_plan_v2.py`: `13 passed` before `0041b60`.
- Hunter: `tests/test_application_execution_artifact.py tests/test_native_resume_artifact_v5.py`: `7 passed` at `0041b60`.
- Apply: `ruff check apps/native-host`, Prettier, build, focused loop/handoff tests (`13 passed`), and native-host suite (`170 passed, 2 third-party warnings`) at `f07ba36`.

Python 3.12.14 is installed. Each mission worktree has an ignored `.venv`.

## Phase 3 current implementation

`app/application_execution_artifact.py` provides:

- `native(application_id, version_id)`: projects the immutable Native V5 PDF artifact without submission authority.
- `n8n(application_id, job_id, callback)`: validates explicit n8n provenance, immutable reference/SHA, one-page evidence, and Candidate Truth/job bindings. It never accepts a mismatched job or labels n8n as Native.

Remaining Phase 3 work:

1. Persist this contract in an additive Hunter migration (`043+`) rather than only projecting it.
2. Connect the Native preparation path to use it.
3. Connect the existing authoritative n8n callback path in `app/api.py` to normalize a completed callback only when it carries the required immutable artifact fields.
4. Add wrong-owner/stale binding/idempotency persistence tests.

## Highest-priority remaining work

1. Harden Plan V2 sensitivity, secure-reference scope, owner digest, provider host policy, and concurrency.
2. Harden signed transport replay/owner validation.
3. Surface the existing Apply `CompleteApplicationLoopService` through owner-authenticated FastAPI commands; `main.py` still exposes only health and generic events.
4. Make `PlanBrowserAdapter` checkpoint-aware with a persisted event sink, safe redirects/review navigation, and direct-submit capability defense.
5. Add controlled real-browser Greenhouse-like fixture; do not use real ATS URLs.
6. Implement receipt/lifecycle, cross-repo fixture, UI orchestration, and CI.

## Safety constraints still in force

No deployment, staging, production mutation, live n8n workflow edits, credentials, Gmail/OAuth, or real ATS/application activity. Final submit remains default-off and can only execute under an explicit durable command in synthetic tests.
