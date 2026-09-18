# MUNSHI modernization baseline — 2026-09-04 UTC

This is a read-only diagnostic baseline. No product, database, workflow, runtime, or deployment source was changed before it was collected.

## Verified facts

| Area | Evidence |
| --- | --- |
| Safe modernization checkout | `/Users/aadil/PROJECTS/munshi-hr-hunter-ui-v2` is clean, points to `4c0f39fa503dabb55ef3212a23d2301ad04ec18a`, and its configured upstream is `origin/feat/cloud-migration-foundation`. |
| Production source ref | Locally available `origin/feat/cloud-migration-foundation` resolves to `d663a65091d03f46beb4400e00b5160be8757e4c`; deployed `4c0f39f` is an ancestor. A live fetch was attempted but the sandbox disallows writing `.git/FETCH_HEAD`. |
| Other local clones | `/Users/aadil/PROJECTS/munshi-hr-hunter` is on `feat/cloud-migration-foundation` at `e932154...` and dirty (four modified files plus an untracked test). It is preserved untouched. `/Users/aadil/PROJECTS/munshi-apply` is at `ccde77f...` on `feat/v3-foundation-alignment` and dirty (modified extension manifest and untracked icons). |
| Production contracts | Source deployment scripts preserve all five Compose layers, n8n read-only paths, WAL-safe SQLite checks, backup/rollback checks, and Hunter-only recreation guards. |
| Crown | `app/assets/munshi_crown.png` and `app/assets/munshi_crown_favicon.png` both hash to the approved `37df80392e8d8a193a991831b6fec94a95bd00313083a384832a75e5f1207e47`. |
| App foundations | FastAPI is `app/api.py`; Streamlit is `app/dashboard.py`/`app/product_shell.py`; SQLite schema and migrations are under `app/database.py` and `migrations/`; canonical n8n workflow is `n8n/workflows/canonical_hr_hunter_workflow.json`. |
| Security foundations | API-secret dependency, Gmail OAuth PKCE/state, encrypted vault (`MUNSHI_VAULT_KEY`), Telegram redaction, and loopback-bound service ports are present. |
| Product foundations | Existing Product UI includes Browse detail/modal behavior, parameterized job querying, profile master-resume distinction, tracker lifecycle mapping, research evidence, and debug/evidence separation. |

## Agent routing record

| Subtask | Agent | Why selected | Escalation |
| --- | --- | --- | --- |
| Repository/worktree, remote-ref, deployment-contract and crown inventory | `munshi_luna` | Bounded read-only discovery and source-contract verification | None |
| Tooling, tests, schema, product, n8n and security inventory | `munshi_luna` | Bounded read-only diagnostics and routine test execution | None |
| Baseline synthesis and next-phase design | Root `munshi_terra` | Integrates findings into implementation sequencing | None |

## Schema and contract inventory

Current operational entities include `jobs`, source-run/health/targeting/query telemetry, `n8n_results`, callback receipts, events, configuration history, settings, Telegram state, and backup/storage metrics. Existing n8n result rows contain ATS/HR scores and artifact URLs; there is no normalized canonical monetary cost-event table. Existing Apply is a separate, dirty repository and has not been modified.

## Pre-existing failures and environment limits

- `pytest -q` and `python3 -m pytest -q` cannot run: pytest is not installed in this environment.
- `python3 -m compileall -q app migrations scripts tools` cannot write to the redirected macOS Python bytecode cache. This is environment-only; source was not changed.
- The production dashboard HTTP sanity request cannot resolve `dashboard.munshi.systems` from this environment, so no HTTP status was observed.
- GitHub CLI is installed but has an invalid authentication token. Recent PR/Actions state could not be queried.
- `git fetch --prune origin` was attempted but cannot write `.git/FETCH_HEAD` under this sandbox. The locally available remote-tracking ref is therefore the verified source ref, not a new network confirmation.

## Assumptions and gaps

- `d663a650...` is treated as the verified local production-branch head pending an environment with writable Git metadata and authenticated GitHub access.
- Live production runtime is not re-verified because DNS is unavailable; no production mutation is authorized or attempted.
- Apply bridge details require source inspection in the separate Apply repository; it remains untouched until a scoped cross-repository change can be made safely.

## No-change contracts

Keep crown hashes unchanged; preserve V2.2 modal semantics; preserve all five Compose layers, WAL-safe backup, exact-SHA/rollback transport safeguards, n8n/Ollama non-recreation, existing n8n authority, Apply boundary, Telegram/scheduler/coordinator/targeting gates, and production data integrity.

## Baseline conclusion

The isolated UI checkout is the appropriate safe starting point. The first implementation work is limited to additive, feature-flagged Phase 0 source QA remediation only if a regression is proven, followed by an additive tenant foundation. No deployment is planned from this baseline.
