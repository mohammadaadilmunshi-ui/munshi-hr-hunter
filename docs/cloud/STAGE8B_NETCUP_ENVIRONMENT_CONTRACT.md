# Stage 8B Netcup Shadow Environment Contract

`config/netcup_shadow_environment_contract.json` is the machine-readable authority for the initial Netcup shadow. `.env.netcup.shadow.example` is placeholders-only and may be copied on the server to a Git-ignored secrets location, never committed with values. `scripts/validate_netcup_shadow.py` cross-checks both artifacts against the real Compose and Hunter supervisor sources.

The internal service contract is `hunter:8000`, `n8n:5678`, and `ollama:11434`. `localhost` is reserved for healthchecks inside a container and loopback-only SSH-tunnel listeners on the host; it is not a cross-container route.

The actual supervisor controls remain `HUNTER_ENABLE_TELEGRAM=false`, `HUNTER_ENABLE_DISCOVERY_SCHEDULER=false`, and `HUNTER_ENABLE_COORDINATOR=false`. The additional `*_ENABLED`, `CLOUD_SHADOW_MODE`, and `PRODUCTION_STATE_IMPORTED` variables are explicit safety assertions used by deployment and verification. They do not silently replace source controls.

Initial host exposure is limited to SSH. FastAPI, Streamlit, and n8n bind host loopback only, suitable for `ssh -L`; Ollama has no host mapping. No production state, credential, callback authority, scheduler authority, DNS, or cutover is in scope.

Production Mac mutation count: `0`.
