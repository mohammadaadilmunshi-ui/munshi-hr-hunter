# Stage 4 — Portability Foundation

This tranche makes the sanitized source portable across macOS and a later
Linux/container runtime. It does not deploy services, change databases, or
rewrite the canonical n8n workflow.

## What changed

- Added `app/platform_config.py` as the central runtime path and endpoint
  abstraction.
- Refactored `app/runtime_config.py` to prefer environment-resolved endpoints
  and state paths, while retaining database-backed settings and macOS
  `launch_agent_plist()` compatibility.
- Made `database.py` honor the portable project root/database resolution.
- Removed personal absolute directories from the canonical integration policy.
- Made the startup wrappers use configurable service hosts/ports and skip
  LaunchAgents, `launchctl`, browser opening, and macOS Ollama app control on
  non-Darwin systems.
- Added an offline n8n contract and validator. The canonical workflow remains
  the source representation for workflow identity and review.

## Environment contract

The names and safe placeholders are listed in `.env.example`. Important
portable settings are:

| Area | Variables |
| --- | --- |
| Project/state | `AADIL_HR_HUNTER_PROJECT`, `DATABASE_PATH`, `AADIL_HR_HUNTER_RUNTIME`, `AADIL_HR_HUNTER_LOGS` |
| FastAPI | `FASTAPI_HOST`, `FASTAPI_PORT` |
| Streamlit | `STREAMLIT_HOST`, `STREAMLIT_PORT` |
| n8n | `N8N_HOST`, `N8N_PORT`, `N8N_BASE_URL`, `N8N_USER_FOLDER`, `N8N_DATABASE_PATH` |
| Ollama | `OLLAMA_ENABLED`, `OLLAMA_REQUIRED`, `OLLAMA_BASE_URL` (or host/port) |
| Scheduling | `AADIL_HR_HUNTER_PLATFORM`, `SCHEDULER_BACKEND` |

Relative paths are resolved under the project root. Defaults preserve the
current development shape: `data/hunter.db`, localhost service binding,
`~/.n8n` for n8n state, and `~/.aadil_hr_hunter_runtime` for controller state.

## Mac compatibility

On Darwin, the existing LaunchAgent plists and `launchctl` lifecycle remain
available. If no integration-health LaunchAgent directory is present, the
runtime resolver falls back to `$HOME/Library/LaunchAgents`. The shell
controllers still support the existing macOS Ollama app and launchd behavior.

## Linux behavior

Non-Darwin mode treats service lifecycle ownership as external. The all-in-one
controller starts services directly when asked, but does not create, load,
unload, or inspect LaunchAgents. A future systemd, supervisor, Kubernetes, or
other process manager can own long-running service restarts and timers through
the same environment contract.

## Ollama policy

Ollama is optional. The default is disabled, so a cloud/default startup must not
require an Ollama binary, app, model, or local port. Set `OLLAMA_ENABLED=true`
to use it; set `OLLAMA_REQUIRED=true` only for a runtime where startup should
fail if the configured Ollama endpoint cannot become healthy. Source behavior
and existing workflow nodes are preserved.

## n8n portability contract

`config/n8n_portability_contract.json` records workflow ID
`L1u2xZkgFpi7KEuv`, the known single-host endpoint assumptions, and the future
private-service variables. `scripts/validate_n8n_portability.py` checks this
contract offline. Stage 5 may render a deployment-specific copy using
`FASTAPI_BASE_URL`, `N8N_BASE_URL`, and `OLLAMA_BASE_URL`; it must not blindly
mutate the canonical JSON or any live n8n instance. In cloud mode, n8n must
reach FastAPI by private service DNS or another explicitly configured internal
URL, including `/api/n8n/status-update` for callbacks.

## Remaining portability debt

- Several legacy workers and dashboard strings still describe or probe
  localhost directly; they are not startup-critical and need a later contract-
  driven endpoint migration.
- The n8n workflow itself still contains Mac/single-host URLs by design.
- Some historical scripts and macOS plist templates retain Mac-specific paths.
- A future deployment must define a process manager, persistent volume policy,
  secret injection, and service-to-service network policy.

## Stage 5 verification

Stage 5 must verify a clean Linux/container build without installing anything
in this tranche, render a deployment-specific n8n workflow copy, exercise
private FastAPI callbacks, verify persistent state mounts, and test the chosen
process manager's restart/health semantics. It must also confirm Ollama is
either intentionally provisioned or absent without breaking default startup.

## Outside Git

Real `.env` files, provider tokens, Telegram credentials/chat IDs, n8n
encryption keys, live SQLite databases, `~/.n8n`, LaunchAgents, production
rollback directories, and the separate live Mac project remain outside this
repository and outside this stage.
