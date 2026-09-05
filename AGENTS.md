# MUNSHI HR Hunter — Repository Agent & Production Deployment Safety Contract

This file applies to the entire repository unless a deeper `AGENTS.md` explicitly narrows a subdirectory.

**Any human, ChatGPT session, Codex agent, Claude agent, GitHub Action, or deployment automation working on this repository must read this file before planning or executing a staging or production deployment.**

The purpose of this contract is to prevent a repeat of the September 5, 2026 production/staging outage and to preserve the proven MUNSHI cloud runtime, databases, deployment authority, and rollback paths.

---

## 1. Deployment authority and operating model

- Netcup is the production runtime.
- GitHub is the source and deployment authority being established for controlled releases.
- The Mac must not silently become a second production authority.
- Production promotion must be deliberate, exact-SHA based, reviewable, and reversible.
- Never assume the current GitHub branch is identical to the live Netcup checkout. Verify both.
- Never reconstruct or invent a missing production commit from memory. If Netcup contains a proven commit that GitHub lacks, transfer the exact Git object and preserve its SHA.

Normal source flow:

`feature branch -> pull request -> CI -> review -> staging -> staging verification -> explicit production promotion by exact SHA`

Emergency flow:

`read-only diagnostics -> exact root cause -> rollback proof -> minimal repair -> runtime proof -> synchronize exact repaired commit back to GitHub -> review -> canonicalize`

---

## 2. September 5, 2026 incident — what happened

### Production failure

A temporary production/staging recovery bootstrap was left active in the production Hunter runtime after its one-time host bootstrap purpose had already been completed.

The temporary runtime changed production behavior in three critical ways:

1. Compose forced Hunter to start as root with `user: "0:0"`.
2. A bootstrap flag remained enabled and `/opt/munshi/bin` was mounted as `/host-bin`.
3. The entrypoint manually dropped from root to the `hunter` user with Python `setuid`/`setgid`, cleared supplementary groups, and did not normalize the root-oriented environment.

That created two production defects at once:

- The process UID became the `hunter` UID, but `HOME` still pointed to `/root`. Streamlit therefore attempted to access `/root/.streamlit/secrets.toml` and failed with `PermissionError` because the non-root Hunter user cannot traverse `/root`.
- Supplementary groups were cleared, so Hunter lost the group access required to traverse/read the read-only n8n mount at `/app/n8n-readonly`.

Streamlit is a required Hunter lane. When Streamlit exited, the supervisor terminated the Hunter container. Docker's restart policy then started it again, producing the same failure repeatedly. FastAPI and Telegram were able to start during each cycle, but were taken down as collateral when the required Streamlit lane failed.

The proven repair restored only the normal runtime forms of:

- `Dockerfile`
- `compose.yaml`
- `docker/hunter-entrypoint.sh`

The verified production repair commit was:

`b22a5c999de3ff1405c5513dd8ba1d76c4cab867`

That SHA is a historical incident reference, not a permanent deployment target.

### Staging failure

Staging had a separate failure. A recovery gateway script was written for Bash but was validated/executed through POSIX `sh`. Bash syntax such as `[[ ... =~ ... ]]` is not valid POSIX `sh`, so staging Hunter exited with code 2.

### Primary lessons

- One-time recovery/bootstrap behavior must never remain enabled as steady-state production configuration.
- Never launch Hunter as root and then manually imitate Docker's normal non-root user setup unless there is an explicit, tested reason and the full environment/group contract is preserved.
- Shell scripts must be parsed/executed by the interpreter they declare in their shebang.
- A deployment is not successful merely because `docker compose up` returned zero. Runtime semantics must be verified afterward.

---

## 3. Hard production prohibitions

The following are prohibited unless there is a separately approved, evidence-backed incident procedure that explicitly requires them:

1. Do not use `user: "0:0"` for normal Hunter production runtime.
2. Do not leave recovery/bootstrap flags enabled in production steady state.
3. Do not leave `/host-bin` mounted into Hunter after a one-time bootstrap has completed.
4. Do not manually drop privileges from root with `os.setuid`, `os.setgid`, or `os.setgroups([])` as a substitute for the normal Docker `USER hunter` runtime contract.
5. Do not weaken `/root`, secret, database, or volume permissions to make a permission error disappear.
6. Do not use broad `chmod`/`chown` fixes on production state without exact proof of ownership requirements.
7. Do not run `docker compose down -v`.
8. Do not run `docker volume rm` or delete production volumes.
9. Do not replace the production Hunter database as part of an application deployment.
10. Do not copy an older Mac/local database over the cloud production database.
11. Do not recreate n8n, Ollama, Caddy, or unrelated containers as a side effect of a Hunter-only deployment.
12. Do not expose Hunter/FastAPI, Streamlit, n8n, or Ollama administrative ports directly to the public Internet.
13. Do not enable automatic production deployment on ordinary `push`, `pull_request`, or timer events.
14. Do not deploy an unreviewed branch name without pinning the exact 40-character Git SHA.
15. Do not leave production in an unexplained dirty working-tree state.
16. Do not treat a temporary branch whose purpose contains words such as `bootstrap`, `recovery`, `incident`, `temporary`, or `diagnostic` as permanent production state without explicit steady-state review.
17. Do not use `sh` to validate or execute a Bash script. Honor the file's shebang/interpreter contract.
18. Do not declare a repeating/scheduled lane failed merely because it has no live PID at one arbitrary instant. Verify the lane's designed semantics and supervisor logs.

---

## 4. Proven production runtime identity contract

Normal Hunter runtime must use the image/container's non-root `hunter` identity directly.

Expected principles:

- Hunter runs as the intended non-root user.
- `HOME` resolves to the Hunter user's home, not `/root`.
- `pathlib.Path.home()` and equivalent user-home lookups must not resolve to `/root` for Hunter.
- Required group/read permissions for the n8n read-only mount must remain intact.
- Hunter must not gain write access to n8n's database merely to make reconciliation succeed.

After any identity, entrypoint, Dockerfile, or Compose change, verify inside the live/recreated Hunter container:

- effective UID/GID are the expected Hunter values;
- home resolution is the Hunter home;
- `/root` is not required by Streamlit;
- `/app/n8n-readonly/database.sqlite` can be opened read-only;
- the n8n mount remains read-only.

---

## 5. Five-layer production Compose contract

Any production Hunter render/recreation must preserve all five layers unless a reviewed migration explicitly replaces this model:

1. `/opt/munshi/repo/compose.yaml`
2. `/opt/munshi/repo/compose.netcup-shadow.yaml`
3. `/opt/munshi/runtime/stage10-imported.override.yaml`
4. `/opt/munshi/runtime/stage12-production.override.yaml`
5. `/opt/munshi/runtime/stage12-n8n-runtime-repair.override.yaml`

The fifth layer preserves the Hunter read-only n8n state contract, including:

- `N8N_USER_FOLDER=/app/n8n-readonly`
- `N8N_DATABASE_PATH=/app/n8n-readonly/database.sqlite`
- n8n state mounted read-only at `/app/n8n-readonly`

Before recreating Hunter, render the complete multi-layer Compose configuration and inspect the effective configuration, not just the base YAML.

---

## 6. Mandatory preflight before any production mutation

Before production is changed, perform a read-only preflight and record evidence for all of the following:

### Source

- exact Netcup HEAD SHA;
- active Netcup branch;
- repository clean/dirty state;
- exact proposed GitHub SHA;
- relationship between current production SHA and proposed SHA;
- proposed changed-file list.

### Runtime

- Hunter container state, health, restart count, OOM state;
- n8n container state, health, restart count;
- Ollama container state, health, restart count;
- edge/Caddy state when relevant;
- current production environment flags;
- current effective Compose render.

### Data safety

- Hunter DB location and integrity status;
- n8n DB read-only integrity status when applicable;
- rollback/backup availability;
- explicit proof that no volume deletion is planned.

### Authority/safety

- Mac is not simultaneously running a second production Telegram listener/scheduler/coordinator;
- rollback path is known;
- unrelated services will not be recreated;
- staging has passed or a documented incident exception exists.

If any of those are unknown, stop and diagnose. Do not patch through uncertainty.

---

## 7. Staging-first rule

For ordinary application/code changes:

1. Deploy the exact candidate SHA to staging first.
2. Keep staging production side effects disabled.
3. Verify staging runtime health and application behavior.
4. Only then explicitly promote the exact approved SHA to production.

Staging must not write to the production Hunter database. Staging must not send production Telegram messages, production callbacks, or production scheduler/coordinator side effects unless a narrowly scoped test explicitly requires and controls them.

Known staging safety intent includes disabled production side-effect flags such as:

- `HUNTER_ENABLE_TELEGRAM=false`
- `HUNTER_ENABLE_DISCOVERY_SCHEDULER=false`
- `HUNTER_ENABLE_COORDINATOR=false`
- `PRODUCTION_CALLBACKS_ENABLED=false`
- `PRODUCTION_STATE_IMPORTED=false`

Verify effective runtime values rather than assuming a file contains them.

---

## 8. Exact-SHA production promotion rule

Production deployment must use an exact 40-character Git SHA.

Before applying it, verify:

- the SHA exists on GitHub;
- it belongs to the approved branch/PR lineage;
- the PR/diff matches the intended scope;
- CI required for that scope has passed;
- staging proof corresponds to that SHA;
- production is not silently ahead of or divergent from GitHub.

If production contains a proven commit that GitHub does not contain, preserve the exact commit identity by transferring the Git object. Do not recreate the same file contents in a new commit and call it equivalent if exact provenance matters.

---

## 9. Hunter-only deployment isolation

A normal Hunter code deployment should recreate/build Hunter only.

Capture n8n and Ollama identity before deployment, including container ID, start time, and restart count. After the Hunter deployment, prove those values are unchanged unless their recreation was explicitly part of the approved scope.

A successful Hunter deployment must not casually restart:

- n8n;
- Ollama;
- Caddy/edge;
- staging services;
- unrelated production containers.

---

## 10. Mandatory post-deployment verification

A deployment is not complete until runtime verification passes.

At minimum verify:

### Container/runtime health

- Hunter running and healthy;
- Hunter restart count stable, preferably 0 after recreation;
- OOM false;
- n8n healthy;
- Ollama healthy.

### Application endpoints

- FastAPI `/health` returns 200;
- Streamlit `/_stcore/health` returns 200;
- n8n `/healthz` returns 200;
- staging equivalents return 200 when staging is part of the operation.

### Long-lived Hunter lanes

Verify appropriate steady-state processes such as:

- Hunter supervisor;
- FastAPI using `app.api_device_auth:app`;
- Streamlit dashboard;
- Telegram listener when enabled.

### Repeating lanes

Discovery and coordinator are repeating lanes, not necessarily permanent live child processes.

Accept their designed states, for example:

- running now; or
- completed successfully and waiting for the configured next interval.

Do not fail a deployment solely because coordinator has no PID after a successful run. Check supervisor logs for semantic state.

Current historical production cadence at the September 5, 2026 incident was:

- discovery: `HUNTER_DISCOVERY_INTERVAL_SECONDS=300`;
- coordinator: `HUNTER_COORDINATOR_INTERVAL_SECONDS=3600`.

Those values are configuration, not immutable requirements. Any cadence change must be deliberate and reviewed rather than accidentally introduced during an incident repair.

### Database integrity

- Hunter DB `PRAGMA quick_check` passes;
- n8n database can be opened read-only and its `PRAGMA quick_check` passes where safe/applicable.

### Incident signature regression checks

After identity/entrypoint/Compose changes, explicitly prove the absence of:

- `Permission denied: '/root/.streamlit/secrets.toml'`;
- `n8n queue reconciliation warning: PermissionError` caused by lost read access;
- `Required Hunter lane exited: streamlit (1)`;
- unexpected restart loops;
- OOM events.

### Production isolation

- n8n unchanged unless intentionally deployed;
- Ollama unchanged unless intentionally deployed;
- no database replacement;
- no volume deletion.

---

## 11. Shell/interpreter contract

Every shell script must be validated using the interpreter it declares.

Examples:

- `#!/usr/bin/env bash` -> validate with `bash -n`, execute with Bash.
- `#!/bin/sh` -> validate with `sh -n`, keep syntax POSIX-compatible.

Never assume a Bash script is valid under `sh` simply because both are shell interpreters.

CI and deployment helpers should include interpreter-correct syntax validation for scripts they execute.

---

## 12. Temporary bootstrap/recovery lifecycle

Any bootstrap/recovery feature must have all of the following before it is allowed near production:

- explicit purpose;
- exact activation condition;
- exact deactivation condition;
- proof it cannot remain accidentally enabled after success;
- rollback behavior;
- post-bootstrap steady-state verification;
- a clear owner for removing/demoting temporary runtime configuration.

Whenever a bootstrap performs its one-time host work successfully, the production runtime must return to normal steady-state configuration in the same controlled procedure unless there is a documented reason not to.

A bootstrap branch is not a production release branch by default.

---

## 13. Phone / ChatGPT / remote-agent deployment rule

A phone session or conversational agent must not be given an ambiguous instruction such as only:

`deploy this to production`

Instead, the deployment request should explicitly require this contract. Recommended instruction:

> Read the repository `AGENTS.md` production deployment safety contract first. Run read-only preflight diagnostics. Use staging first for ordinary changes. Deploy production only by the exact approved Git SHA using the canonical MUNSHI deployment path. Do not invent a new deployment method, do not enable temporary bootstrap behavior as steady state, do not touch databases/volumes, do not recreate n8n/Ollama/Caddy unless explicitly in scope, and stop on any failed verification gate.

If an agent cannot access or verify the current repository/runtime state, it must ask for or run diagnostics rather than guessing.

---

## 14. GitHub / agent working style

For agent-authored changes:

- use a dedicated branch;
- keep changes narrowly scoped and reversible;
- prefer a draft PR while runtime assumptions are unresolved;
- document production impact and rollback behavior;
- do not weaken checks just to make CI green;
- add focused regression tests for deployment defects being fixed;
- do not merge a PR with unresolved synchronization blockers;
- never expose secrets, credentials, private keys, or production `.env` values in commits, logs, PRs, or chat output.

Production-sensitive changes include at least:

- `Dockerfile`;
- `compose*.yaml`;
- Hunter entrypoint/supervisor code;
- deployment scripts;
- runtime environment contract;
- volume mounts;
- user/group/permission handling;
- edge/auth configuration;
- scheduler/coordinator/Telegram enablement;
- GitHub Actions deployment transport.

Treat these as high-risk even when the diff is small.

---

## 15. Required checks before claiming deploy-ready

At minimum, as applicable to the change:

- Python compile/import checks pass;
- focused pytest suite passes;
- Docker image builds on Ubuntu x86_64;
- shell syntax checks pass with the correct interpreter;
- Netcup foundation/runtime validation passes;
- Deployment Transport Guard passes when deployment transport changes;
- no tracked secrets/databases/private keys are introduced;
- five-layer production Compose tokens remain preserved;
- no automatic production trigger is introduced;
- staging verification passes for ordinary releases;
- exact-SHA provenance is known.

---

## 16. Stop conditions

Stop and surface evidence instead of improvising through any of the following:

- database corruption or failed integrity check;
- unknown encryption/decryption state;
- unavailable rollback path;
- duplicate Telegram listener;
- simultaneous Mac and cloud production authority;
- destructive database/volume operation;
- unexpected dirty production repository;
- unexplained branch/SHA mismatch;
- security-sensitive unknown state;
- required service unhealthy before deployment without a diagnosed cause;
- GitHub history that cannot be reconciled with proven production lineage;
- temporary bootstrap/recovery behavior whose steady-state consequences are unknown;
- identity/home/group changes that have not been tested inside the container;
- post-deploy endpoint or runtime verification failure.

When a stop condition occurs, diagnose first. Do not stack another production patch on top of an unverified partial state.

---

## 17. Anti-regression summary

Before touching production, remember the September 5 incident:

**The host was healthy. n8n and Ollama were healthy. The outage came from a small temporary runtime change that altered process identity semantics and a separate staging shell-interpreter mismatch.**

The prevention rule is therefore simple:

**Prefer exact, minimal, reversible changes. Preserve the normal non-root runtime. Keep temporary bootstrap behavior temporary. Honor interpreter contracts. Stage first. Deploy exact SHAs. Verify real runtime semantics after every production change.**
