# Stage 8B Netcup Baseline Diagnostic

- Timestamp: 2026-08-31 (America/New_York)
- Repository: `https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter`
- Branch: `feat/cloud-migration-foundation`
- Repository HEAD: `274feb4dcd1cdf780e49cf8c1b8aede168c6b584`
- Authoritative Stage 6B ancestor: `274feb4dcd1cdf780e49cf8c1b8aede168c6b584` (verified)
- Main SHA: `374d9ae2a9a0cb8fa85825803a9c2f25205b8866`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Expected cloud architecture: Linux `x86_64`/`amd64`
- Expected provider contract: Ubuntu 24.04 LTS guest on RS 2000 G12 KVM, 8 dedicated AMD EPYC 9645 cores, 16 GB RAM, and 512 GB NVMe SSD backing
- Production Mac mutations: `0`

## Authority and repository state

The working tree was clean at the start of Stage 8B. `HEAD` was exactly the authoritative Stage 6B head, the feature branch tracked its same-named origin branch, and both local and recorded remote `main` matched the immutable baseline. The canonical workflow ID is `L1u2xZkgFpi7KEuv`; legacy workflow `GfiDMrb94BFJXq1D` is not the cloud authority. The canonical JSON is an immutable input and cloud deployment must use only the renderer-generated copy.

## Current container architecture

| Service | Image/build | Startup | Internal endpoint | Host access | Persistence |
|---|---|---|---|---|---|
| `hunter` | Local `Dockerfile`, Python 3.12 slim Debian | `docker/hunter-entrypoint.sh` -> `docker/hunter-supervisor.py` | FastAPI `hunter:8000`; Streamlit `hunter:8501` | `127.0.0.1:8000`, `127.0.0.1:8501` | `hunter_data`, `hunter_runtime`, `hunter_logs` |
| `n8n` | `n8nio/n8n:2.22.5` | Image default | `n8n:5678` | `127.0.0.1:5678` | `n8n_data` |
| `ollama` | `ollama/ollama:latest` | Image default | `ollama:11434` | none | `ollama_models` |

All services share the Compose `application` bridge network. Compose volume names are project-scoped; no production path or state is bind-mounted. FastAPI and Streamlit are required Hunter supervisor lanes. Telegram, discovery scheduling, and the unified coordinator are opt-in lanes and are disabled by default. Discovery and coordinator lanes are mutually exclusive.

## Environment contracts

Container service routing is based on `FASTAPI_BASE_URL=http://hunter:8000`, `N8N_BASE_URL=http://n8n:5678`, and `OLLAMA_BASE_URL=http://ollama:11434`. The HR Agent adapter uses `gemma3:4b`, requires real Ollama output, and is invoked through the FastAPI proxy in subprocess isolation. Dice/Playwright uses `/usr/bin/chromium`. `HUNTER_API_SECRET` is required when FastAPI imports; the n8n encryption key and all production credentials are runtime-only and never stored in Git.

The source-level production lane controls are `HUNTER_ENABLE_TELEGRAM`, `HUNTER_ENABLE_DISCOVERY_SCHEDULER`, and `HUNTER_ENABLE_COORDINATOR`. The Stage 8B contract must preserve those names while adding explicit shadow assertions for scheduler, callbacks, and state-import status.

## n8n portability contract

The renderer verifies the canonical SHA before replacing eight classified loopback endpoint occurrences in a separately named output file. It targets the Docker DNS names above and preserves semantic metadata that merely contains the word `localhost`. It refuses to overwrite the canonical JSON. The canonical source itself must remain byte-for-byte unchanged.

## Safety boundaries

The migration repository is the only local write scope. Mac production code, databases, n8n state, LaunchAgents, processes, credentials, browser state, Docker state, logs, and environment files are read/write denied by policy. Cloud work is shadow-only: no production state import, live Telegram, real discovery, scheduler, coordinator, production callback activity, DNS change, public dashboards, billing action, merge, or cutover is permitted.

New local Docker validation must use a unique Compose project name, synthetic secrets and data, dynamically allocated or loopback-only ports, and new project-scoped volumes. The retained `munshi-stage7-parity_*` volumes are outside the test scope and must not be changed or removed.

## Existing evidence and gaps

The repository contains Stage 6B Docker foundation source, validators, focused tests, and Ubuntu x86_64 CI. The handoff states that Stage 7 local ARM64 container parity passed, including persistence and real Ollama/Chromium/HR Agent checks; however, no Stage 7 evidence artifact is committed in this repository. This diagnostic records the handoff claim without treating an absent file as fresh proof.

Known cloud risks and remaining blockers are:

- Netcup provisioning, IP address, datacenter, and initial SSH access are not yet supplied.
- Actual OS, architecture, CPU, RAM, presented block capacity, root free space/filesystem, and network must be forensically verified before remote mutation. Guest device naming and `ROTA` are virtual-presentation metadata, not proof of the provider's physical NVMe backing.
- Long-duration n8n 2.22.5 ARM64 stability is not proven; the intended Netcup runtime is x86_64.
- `ollama/ollama:latest` is not digest-pinned; the deployed architecture and model must be inspected and recorded.
- The base Compose layer lacks cloud restart policies, bounded logging, and explicit comprehensive shadow flags.
- No repository-owned Netcup bootstrap, deployment, authoritative validator, benchmark, reboot proof, or endurance watcher existed at this baseline.
- No production state or credentials may be accessed during Stage 8B/9; their absence is intentional, not a test failure.
- Stage 9 cannot begin until an explicit host and valid SSH identity are provided.

## Baseline conclusion

The Stage 6B source is a suitable foundation for an additive Netcup shadow layer. The next work is cloud-only configuration and tooling; the proven base Compose topology and immutable canonical workflow must remain intact.

Safety status: `PASS`
Production Mac mutation count: `0`
