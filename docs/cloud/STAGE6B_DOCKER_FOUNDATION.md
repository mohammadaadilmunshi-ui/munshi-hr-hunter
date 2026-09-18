# Stage 6B Docker Foundation

Stage 6B establishes a source-only container foundation after Stage 6 source remediation. It targets Ubuntu x86_64, which is proven. ARM64 Linux remains unproven.

## Responsibilities

- `hunter` contains Python 3.12, FastAPI, Streamlit, the Telegram runtime, the proven discovery/coordinator modules, the versioned `integrations/hr_agent/n8n_hr_score.py` adapter, Playwright, and Debian Chromium.
- `n8n` is exactly `2.22.5` and owns its persistent n8n state volume.
- `ollama` is an internal-only runtime with a persistent model volume. This stage never pulls `gemma3:4b`; Stage 7 parity must explicitly provision and verify it.

The HR Agent adapter requires reachable Ollama. Its direct/legacy n8n Ollama lane remains independently optional. Dice uses Playwright with `/usr/bin/chromium`. No full HackerRank repository or runtime is required.

## Hunter runtime

There was no existing single Linux-safe runtime entrypoint. `docker/hunter-supervisor.py` therefore runs the proven FastAPI and Streamlit lanes in the foreground, handles SIGTERM/SIGINT, propagates shutdown, and fails if a required lane exits. Telegram and scheduling are opt-in with `HUNTER_ENABLE_TELEGRAM`, `HUNTER_ENABLE_DISCOVERY_SCHEDULER`, or `HUNTER_ENABLE_COORDINATOR`. The two scheduler flags are mutually exclusive to prevent duplicate schedulers. CI never starts these lanes.

## Boundaries

Named volumes hold Hunter data/runtime/logs, n8n state, and Ollama models. No SQLite database, browser profile, Downloads/Documents directory, Mac home path, LaunchAgent, or live n8n state is copied or mounted. Secrets are runtime-only: later migration must inject `HUNTER_API_SECRET`, the n8n encryption key, provider credentials, Telegram secrets, and Google credentials. None are populated here.

Compose uses private service DNS (`hunter`, `n8n`, and `ollama`) and loopback-only local parity ports for FastAPI, Streamlit, and n8n. Ollama is not publicly mapped.

## n8n workflow model

`n8n/workflows/canonical_hr_hunter_workflow.json` remains immutable at its Stage 6 SHA-256. The Stage 6 renderer must be run offline to produce a separately named deployment copy with container endpoints; that copy may be reviewed for a later import. This stage does not import into live n8n and does not rewrite the canonical JSON.

## Scope and next stage

This is source-only plus CI build proof. It does not migrate state, start production integrations, download a model, deploy, or cut over production. Stage 7 is local/container parity with disposable state. The Mac remains production authority.
