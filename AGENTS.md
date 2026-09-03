# MUNSHI HR Hunter — Repository Agent Contract

This file applies to the entire repository unless a deeper `AGENTS.md` explicitly narrows a subdirectory.

## Mission

Improve MUNSHI HR Hunter through reviewable, reversible source changes while preserving the production cloud runtime and its evidence contracts.

## Current production boundary

Netcup is the production runtime. GitHub is being upgraded to become the source/deployment authority, but production deployment must remain explicit and controlled.

Do not assume the GitHub branch is identical to the live server. During the cloud-migration transition the proven Netcup repository may be ahead of GitHub. Never invent or reconstruct missing production commits from memory.

## Mandatory safety rules

1. Never commit or generate live databases, SQLite/WAL/SHM files, secrets, tokens, credentials, private keys, production `.env` files, user resumes, or other private runtime state.
2. Never run or add an automatic production deployment on `push`, `pull_request`, or a timer. Production promotion must remain a deliberate manual action using an exact commit SHA.
3. Never use `docker compose down -v`, `docker volume rm`, or any destructive volume command in production tooling.
4. Never re-enable Mac production authority as part of a normal code change.
5. Never expose Hunter/FastAPI, Streamlit, n8n, or Ollama administrative ports directly to the public Internet. Streamlit remains loopback behind an authenticated HTTPS reverse proxy. Ollama remains internal-only.
6. Never recreate n8n or Ollama as a side effect of a normal Hunter code deployment.
7. Never copy an older local/Mac database over the cloud production database.
8. Never publish or replace the canonical n8n workflow merely because current/published version IDs differ.
9. Never remove Telegram, scheduler, coordinator, targeting, ATS/HR quality gates, or n8n callback safeguards without explicit scope and regression evidence.

## Proven production Compose contract

Any future production Hunter recreation must preserve all five layers:

1. `/opt/munshi/repo/compose.yaml`
2. `/opt/munshi/repo/compose.netcup-shadow.yaml`
3. `/opt/munshi/runtime/stage10-imported.override.yaml`
4. `/opt/munshi/runtime/stage12-production.override.yaml`
5. `/opt/munshi/runtime/stage12-n8n-runtime-repair.override.yaml`

The fifth layer is critical. It preserves the Hunter read-only n8n state contract:

- `N8N_USER_FOLDER=/app/n8n-readonly`
- `N8N_DATABASE_PATH=/app/n8n-readonly/database.sqlite`
- n8n named volume mounted at `/app/n8n-readonly` read-only

## Production deployment model

Source changes should normally follow:

`branch → pull request → CI → review → staging/verification → explicit promotion`

Production promotion must:

- use an exact 40-character Git SHA;
- verify that SHA belongs to the approved source branch;
- refuse a dirty production working tree;
- verify no unsafe active/manual worker state;
- preserve rollback evidence;
- build/recreate Hunter only;
- prove n8n and Ollama container identity/start time did not change;
- run post-deploy health and database checks;
- roll back automatically if the Hunter deployment fails.

## GitHub / Codex working style

For agent-authored changes:

- work on a dedicated feature branch;
- keep changes scoped and reversible;
- prefer a draft PR while migration/runtime assumptions are unresolved;
- explain production impact and rollback in the PR;
- do not merge a PR with known synchronization blockers;
- run the existing Linux and Docker compatibility workflows;
- add focused regression tests for defects being fixed;
- treat CI failures as evidence to diagnose, not as checks to weaken.

## Required checks before claiming deploy-ready

At minimum:

- Python compile/import checks pass;
- focused pytest suite passes;
- Docker image builds on Ubuntu x86_64;
- Netcup foundation validator passes;
- shell syntax checks pass;
- JobSpy image/runtime contract passes when that production history is present;
- no tracked secrets/databases/private keys are introduced;
- five-layer production tokens remain in deployment tooling;
- no automatic production trigger is introduced.

## JobSpy anti-regression contract

The proven cloud repair expects:

- `python-jobspy==1.1.82`
- runner path `/app/hunter/tools/runners/jobspy_runner.py`
- runner SHA-256 `4904e6456eb721ac80d3ff8c62001c4a7fc13c5152309be09135e47538b56661`
- Linux/container interpreter resolution that can use the proven container Python runtime

Do not copy a Mac virtual environment into the container.

## Staging principle

A future staging dashboard must use an independent Compose project and must not have production side effects enabled. Do not point staging at the production Hunter database in read-write mode.

## Stop conditions

Stop and surface evidence instead of patching through any of these:

- database corruption or failed integrity check;
- unknown encryption/decryption state;
- unavailable rollback path;
- duplicate Telegram listener;
- simultaneous Mac and cloud production authority;
- destructive volume/database operation;
- unknown security-sensitive state;
- GitHub history that cannot be reconciled with the proven production commit lineage.
