# MUNSHI Netcup and Infrastructure Navigation for Codex
**Evidence date: October 15, 2026**  
**Purpose:** Find the real system, diagnose its issues, and build safely without confusing the Mac, GitHub, staging, or production. This document does NOT authorize deployment, production database changes, job submissions, or application retries.

## 1. Read before doing anything

Read these in order:
1. AGENTS.md in the Hunter worktree (mandatory repository safety contract).
2. docs/MUNSHI_INFRASTRUCTURE_OPERATIONS_CODEX_NAVIGATION_20261015.md (this document).
3. docs/TSENTA_AUTHENTICATED_MCP_PRECODEX_20261015.md (authenticated Tsenta tool inventory).
4. docs/PRE_CODEX_LINKEDIN_TSENTA_PREP_20261015.md (LinkedIn two-link and bridge design).
5. docs/GITHUB_NETCUP_DEPLOYMENT_FOUNDATION.md (approved production release design).
6. For Apply, read docs/ARCHITECTURE.md, docs/MASTER_ARCHITECTURE.md and README.md from its repo.

Important: observations from older chats are historical. Recheck live states and SHAs; never deploy a remembered SHA.

## 2. Verified topology and current locations

Mac:
- Current clean feature worktree: /Users/aadil/PROJECTS/MUNSHI_LINKEDIN_RESOLVER
- Its GitHub branch: feat/linkedin-employer-url-resolution-20261015
- GitHub Hunter repo: https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter
- Current draft Hunter PR: https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter/pull/103
- Old local Hunter checkout: /Users/aadil/PROJECTS/munshi-hr-hunter
- Old local Apply checkout: /Users/aadil/PROJECTS/munshi-apply
- GitHub Apply repo: https://github.com/mohammadaadilmunshi-ui/munshi-apply
- Both old Hunter and Apply checkouts have uncommitted changes and other worktrees. Do NOT reset/clean them or patch them blindly. Create dedicated feature worktrees after verifying branch lineage.
- Old diagnostic/operator scripts and logs: /Users/aadil/Downloads/MUNSHI_CRESCENT_* ; different scripts have different modes and some can mutate live application state.

Netcup verified through STRICT read-only SSH on October 15:
- IP: 159.195.244.16 ; hostname: v2202609408749510348 ; ordinary SSH user: munshi.
- Mac key file already exists: ~/.ssh/munshi_netcup_ed25519 (private; NEVER print, share, copy, commit or transfer).
- Production Hunter repo: /opt/munshi/repo
- Runtime overrides: /opt/munshi/runtime
- Historical backups: /opt/munshi/backups (NEVER delete)
- Reports: /opt/munshi/reports
- Executable wrappers: /opt/munshi/bin (READ before any execution)
- Staging root: /home/munshi/munshi-staging-v1
- Staging repo: /home/munshi/munshi-staging-v1/repo
- Production Hunter branch when checked: fix/workday-production-readiness-20260926
- Production Hunter SHA prefix: b8cd45ea0; working tree clean on check.
- Mac feature PR may be at a later SHA, including the Tsenta documentation. It is NOT production.
- Verified loopback healthy HTTP 200: 8000/health, 8501/_stcore/health, 5678/healthz, 19000/health.
- Verified containers: munshi-netcup-shadow-hunter-1, munshi-netcup-shadow-n8n-1, munshi-netcup-shadow-ollama-1, munshi-netcup-staging-hunter-1, munshi-netcup-staging-n8n-1, munshi-netcup-staging-ollama-1, munshi-apply-production-apply-1, munshi-apply-production-prepare-worker-1, munshi-apply-staging-apply-1 and munshi-staging-edge-caddy.
- No production submit-worker container appeared in the read-only docker ps snapshot. Diagnose why; do NOT start it based on this observation.
- Root disk was about 57% full. Recheck before migrations or builds.
- Apply backend host-source directory was NOT established; DO NOT invent /opt/munshi-apply or copy code to an assumed folder.
- Dashboard: https://dashboard.munshi.systems
- Staging dashboard: https://staging-dashboard.munshi.systems

## 3. Architecture ownership map

    Mac: Codex / Git worktrees / SSH identity
      -> GitHub private Hunter and Apply repos / CI / manual release
      -> Netcup cloud runtime
         Hunter FastAPI on 127.0.0.1:8000
         Hunter Streamlit on 127.0.0.1:8501
         n8n on 127.0.0.1:5678 (published workflows/callbacks)
         Ollama internal-only
         Hosted Apply API on 127.0.0.1:19000
         Apply prepare worker + browser/ATS state
         Separate staging containers, DB and edge
      -> MUNSHI Apply extension and owner workspace
      -> Tsenta authenticated MCP on the Mac; NOT authenticated for Netcup backend

The dashboard is the intended user control plane. Terminal use is temporary for end-to-end diagnostics and inspection. Preserve existing Hunter discovery, Candidate Truth, resume/answer quality gates, approvals/eligibility, and the application ledger.

## 4. Read-only SSH access

Run from the Mac terminal:

    ssh -i "$HOME/.ssh/munshi_netcup_ed25519" \
      -o IdentitiesOnly=yes \
      -o BatchMode=yes \
      -o StrictHostKeyChecking=yes \
      -o ConnectTimeout=8 \
      munshi@159.195.244.16

Then safe read-only checks on Netcup:

    hostname; whoami; date -u
    git -C /opt/munshi/repo branch --show-current
    git -C /opt/munshi/repo rev-parse HEAD
    git -C /opt/munshi/repo status --short
    df -h /opt/munshi/repo
    docker ps --format '{{.Names}} | {{.Status}}'
    curl -m 4 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
    curl -m 4 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8501/_stcore/health
    curl -m 4 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5678/healthz
    curl -m 4 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:19000/health

Never disable StrictHostKeyChecking or guess server passwords. Older chats reported that key-based root access may exist, but root is a separate privileged scope and is NEVER an automatic escalation path. Do not expose admin ports publicly. Docker logs and environment files may contain applicant details or secrets; never dump these wholesale.

## 5. Production Compose: preserve all five layers

The proven Hunter production stack requires:
1. /opt/munshi/repo/compose.yaml
2. /opt/munshi/repo/compose.netcup-shadow.yaml
3. /opt/munshi/runtime/stage10-imported.override.yaml
4. /opt/munshi/runtime/stage12-production.override.yaml
5. /opt/munshi/runtime/stage12-n8n-runtime-repair.override.yaml

The final layer preserves:
- N8N_USER_FOLDER=/app/n8n-readonly
- N8N_DATABASE_PATH=/app/n8n-readonly/database.sqlite
- The n8n named volume mounted read-only to /app/n8n-readonly.

NEVER simplify/remove override layers, recreate n8n or Ollama as a Hunter side effect, overwrite the production DB, wipe WAL/SHM, remove Docker volumes, run docker compose down -v, docker volume prune, docker system prune -a, blindly restart services, or mutate production in an exploratory task.

## 6. Safe GitHub -> Netcup release route

Read AGENTS.md, docs/GITHUB_NETCUP_DEPLOYMENT_FOUNDATION.md, README_DEPLOYMENT_FOUNDATION.md, config/remote-deployment-policy.json and deploy/netcup scripts before anything release-related.

Correct design:
    dedicated branch -> draft PR -> tests + Linux/Docker/transport CI -> isolated staging
    -> current-source reconciliation -> migration + rollback + UI review
    -> explicitly approved exact 40-char SHA via manual GitHub Actions production environment
    -> authenticated Git bundle and restricted SSH forced-command transport
    -> /opt/munshi/bin/deploy-production-release
    -> Hunter-only health/state verification and rollback if failed.

Netcup is not supposed to rely on anonymous outbound GitHub fetch to deploy. Do not run random git pull/reset on production or activate automatic production push deployments. Apply has its own deployment contract: Hunter wrapper is not a universal Apply deployment tool.

## 7. Local source entry points (verify by reading code)

Hunter:
- app/product_pages.py — Streamlit cards, Original Job, Apply Job, Prepare action.
- app/product_application_flow_v2.py — application UI/business flow.
- app/candidate_profile_authority_v1.py, app/profile_workspace_v4.py — Candidate Truth.
- app/answer_brain_v2.py, app/application_answer_router_v1.py — answer sources.
- app/application_preflight_package_v1.py, app/autonomous_prepare_v1.py, app/prepare_orchestrator_v1.py, app/prepare_runtime_worker.py — preparation state.
- app/apply_handoff.py — Hunter to Apply interface.
- app/linkedin_batch_enrichment_v6.py, app/linkedin_verified_url_evidence_v5.py — draft LinkedIn exact-job matching/evidence.
- app/tsenta_profile_bridge_v1.py — inert comparison/handoff; not a live Tsenta adapter.

Apply repo:
- docs/ARCHITECTURE.md, docs/MASTER_ARCHITECTURE.md, README.md
- apps/extension/src/content, apps/extension/src/background, apps/extension/src/sidepanel
- apps/extension/public/manifest.json
- apps/native-host/src/munshi_apply_native
- apps/owner-workspace
- packages/contracts, packages/semantic-engine, packages/application-model

The currently inspected local extension manifest is version 0.2.11. Prior discussion reported 0.2.12 installed. Treat this as an unresolved source-versus-installed version mismatch and investigate before upgrading. Apply's local README/architecture may describe manual-only submission controls; actual hosted and extension release behavior must be verified separately.

The Mac did not have rg in the latest terminal probe. Use portable grep/find if necessary:

    grep -RIl --include='*.py' --exclude-dir=.git --exclude-dir=.venv \
      -E 'start_prepare_for_job|candidate_truth_sha256|prepare_package_sha256' app | head -40

## 8. How to find the blocker in a real application

Use ONE exact saved job, one user-owned application ID, one preparation operation, and its current source SHA. Do not create duplicate operations just to see whether it works.

    Hunter source + canonical job URL
       -> independently verified employer Apply URL (not the same as original LinkedIn)
       -> targeting and work authorization
       -> Candidate Truth/resume/answer digests
       -> durable Prepare Application record
       -> Hunter-to-Apply handoff
       -> queued worker and browser session
       -> ATS page fields, choices and upload evidence
       -> separately authorized final submission
       -> employer confirmation receipt
       -> dashboard/extension state reconciliation.

For every boundary determine current state, persisted IDs, last successful checkpoint, retry count, missing fields, submission command count and receipt evidence. Retain digests/statuses rather than raw personal answers.

Prepare is not Submitted. Filled is not Submitted. Submit click is not an independently confirmed employer receipt. A Tsenta success status alone is not independent receipt evidence.

Historical Workday R441 example: Crescent Energy HR Analyst - Total Rewards / Job 4465 was blocked by prepare/field handling and lacked independently verified employer receipt. Previous notes mention phone/referral issues and multiSelectContainer/multiselectInputContainer ancestor metadata not recognized by input-only inspection. Historical scripts in ~/Downloads include MUNSHI_CRESCENT_FRESH_BROWSER_E2E_20261008.sh, other replay, patch and retry helpers. They may have mutation/submit modes. Read their contents before ANY use; never run old execute or retry modes based on a prior report.

Do NOT assume historical session IDs or approval flags are still valid. Re-check exact current state and duplication before even proposing a real submission.

## 9. n8n, extension and execution authority

- n8n is workflow/callback coordination. Never republish the canonical workflow, enable a second Telegram listener, or start duplicate discovery/schedulers based on a mismatch between local and published workflow IDs.
- The extension needs the same durable application ID, backend status, page diagnostics, and receipt state as the dashboard; do not create a competing permanent ledger.
- Browser actions should be Observe -> Interpret -> Plan -> Act -> Verify -> Rescan across pages. Check actual DOM/ARIA/shadow-root semantics; never trust a screenshot-only guess.
- Never force-click an employer Submit button to hide an unresolved field issue. Unanswered high-risk work authorization and sponsorship questions must not be fabricated.
- Before calling anything “production ready”, verify actual browser extension build, API/workers, staging image, current SHA and CI contract.

## 10. Tsenta integration scope

The local Codex CLI is authenticated through Tsenta MCP over https://api.autojobs.me/api/v1/mcp.
The account's stored profile can be read through authorized resume/profile tools; an exposed apply_to_job tool accepts an external URL by schema. Full field-by-field application learning and verified external job execution are not yet established.
- Use docs/TSENTA_AUTHENTICATED_MCP_PRECODEX_20261015.md for the real tool names.
- Keep MUNSHI Candidate Truth authoritative and compare saved Tsenta fields in memory.
- Do not copy local ~/.codex OAuth files/tokens to Netcup. The backend needs separately supported OAuth authorization and secure tenant-scoped storage.
- During inspection call only permitted read tools, not apply, approve, upload, submit, change preferences or update account tools.
- Learn only from data legitimately returned by authorized user-facing tools. Never reverse engineer private Tsenta credentials, intercept sessions, or infer proprietary backend internals.

## 11. Symptom to first diagnostics

| Symptom | Inspect before patching |
| --- | --- |
| LinkedIn Apply button absent | Official ATS-board enrollment, signed V5 evidence table, current posting match and UI projection |
| Prepare click fails | Job snapshot, Candidate Truth digest, preflight package, durable operation/queue |
| Resume missing | Resume artifact generation, digest binding, worker error, exact document |
| Workday stuck | Real DOM, select/multiselect ancestor, field values and validation state |
| Extension disagrees with dashboard | Shared IDs and state source, installed version, backend API projections |
| Submitted but receipt missing | Confirm employer-side receipt; separate submit intent/provider status |
| SSH fails | Correct SSH user, key path, IdentitiesOnly, host key; do not bypass security |
| SQLite locked | Disk/DB health, process concurrency, read-only diagnostics; no WAL/SHM deletion |
| n8n callback absent | Published workflow, callback auth, duplicate listeners, logs with private values redacted |
| Tsenta profile inconsistent | Actual MCP scope, field statuses, current confirmed Candidate Truth |

## 12. Working contract for Codex

You MAY: read source, use strict read-only Netcup diagnostics, inspect GitHub state, create clean isolated feature worktrees, write scoped tested source code, run unit tests and synthetic/staging workflows without real-world effects, and update draft PR documentation.

You MUST NOT without a separate explicit execution mandate: deploy, migrate production, modify production DB/volumes, enable submit workers, send applications or emails, retry old applications, update Tsenta profile/account settings, change n8n published workflows, tamper with OAuth/secrets, escalate to root, force-reset dirty repos, disable safety/quality gates or invent a receipt.

Stop conditions: corruption, unclear encryption, unproven rollback, Git branch ancestry conflict, staging touching production, simultaneous actors for one job, credential uncertainty, or any unauthorized real employer action.

## 13. Codex startup task

Read AGENTS.md, this guide, and the authenticated Tsenta handoff. Begin with source and Netcup **read-only** diagnostics. Treat the Mac older Hunter and Apply checkouts as dirty. Inspect current SHA, staging, containers, DB/service state and precise owner of each integration boundary rather than guessing from old conversations. Then implement the Tsenta profile comparison, routing and observable-learning connector as small tested changes on isolated worktrees. Preserve the five Compose layers, existing Hunter discovery, n8n, extension/worker authority and the existing dashboard buttons. Do not deploy or submit. Record the actual tests, risks and missing receipts in the draft pull request.

**Note:** This guide documents a dated infrastructure snapshot. Current runtime evidence always takes precedence over old chats, static docs or earlier success claims.
