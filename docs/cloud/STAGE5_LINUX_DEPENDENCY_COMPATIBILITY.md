# Stage 5B — Linux Dependency Compatibility

## Evidence carried from Stage 5A

The production source environment is Python 3.12.14 on arm64 with Node
24.16.0, npm 11.13.0, and n8n 2.22.5. Its pip check and direct import smoke
tests passed. Stage 5A identified 9 direct requirements, a 56-package lock,
67 live packages, missing lock coverage for JobSpy, Beautiful Soup, and
Playwright, and a pandas drift between the lock (3.0.3) and live environment
(2.3.3). Static imports also confirmed httpx, pydantic, and requests.

## Dependency repair strategy

`requirements.txt` declares every third-party distribution directly imported
by active `app/` or `scripts/` Python source, while preserving the prior direct
requirements. `requirements.lock.txt` is the exact known-good production
snapshot supplied for this tranche, including the live pandas and JobSpy
versions. It was aligned for reproducibility, not upgraded. Packages are not
installed on the Mac by this change.

## Runtime and Linux proof contract

`config/runtime_versions.json` is authoritative. It separates the production
source environment (arm64) from the initial GitHub Actions proof target
(Python 3.12 on x86_64 Ubuntu). ARM64 Linux remains explicitly unproven.

The workflow proves lock installation, pip consistency, syntax compilation,
the n8n portability contract, the offline audit, focused tests, and imports of
the direct third-party dependencies. It does not execute workers, browsers,
n8n, Telegram, providers, Docker, migrations, or deployments.

## Portability policies

macOS behavior remains available through explicit Darwin gates. LaunchAgent
files in the repository are templates rendered with the current project and
home paths; no personal `/Users/aadil` path is part of portable source or
configuration. Linux uses the external scheduler mode and does not execute
`launchctl`, `plutil`, `open`, `osascript`, or `/Applications` tooling.

Localhost is retained for same-host development defaults and the canonical Mac
n8n baseline. Active service health and runtime probes use the endpoint
abstraction, so deployments can provide service DNS or private URLs through
environment configuration. The canonical n8n workflow JSON is immutable; a
future renderer may generate a deployment copy without rewriting the baseline.

Ollama is optional. Portable defaults are `OLLAMA_ENABLED=false` and
`OLLAMA_REQUIRED=false`; no model is provisioned or required by CI.

## Remaining blockers before Docker

This tranche does not create container definitions or prove a multi-process
deployment. Before Docker, the service topology, persistent state locations,
secret injection, n8n endpoint rendering, database ownership, and provider
network policy still need an explicitly tested deployment design. ARM64 Linux
also needs a separate proof run.

## Meaning of a passing Actions run

A passing run proves that the exact supplied lock can install on Ubuntu
x86_64, that Python dependencies are internally consistent there, that source
compiles, that portability contracts and focused offline tests pass, and that
direct imports resolve. It does not prove production provider access, browser
runtime behavior, Telegram delivery, n8n execution, database compatibility,
Docker behavior, or ARM64 Linux compatibility.
