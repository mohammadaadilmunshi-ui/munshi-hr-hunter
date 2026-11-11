# Netcup original dashboard failed deployment and narrow recovery — 2026-10-16

Original site: https://dashboard.munshi.systems
Target exact Hunter SHA: 26ad0dec0211255a1de1c905cc703e1a9f815f65
Failed protected workflow: 38917758191 (SSH step exit code 255).

## Verified evidence

Production CI passed. The deployment stopped Hunter for a bounded, verified
3,436,677,649-byte compressed backup, then began 041–059 migrations. The
GitHub remote session exited code 255 without a migration result or rollback
receipt. Root cause at the transport layer is unproven; several minutes of
silent database work are consistent with idle disconnect.

The production DB read-only schema inspection afterwards found every required
Complete Loop, Tsenta OAuth/learning, provider route, callback and notification
table/column. The existing migration image, protected backup and original five
Compose layers remained available. No rollback or deploy process remained.

A guarded Hunter-only Compose recreate with the existing built image restored
Hunter without any new backup or repeated migration. Verified Hunter healthy;
FastAPI /health 200, Streamlit health 200, unauthenticated Tsenta learning API
401, original external dashboard 401 (HTTP Basic Auth). n8n/Ollama/Apply
container identity/start times did not change.

## Future transport hardening

The protected GitHub workflow now sets SSH keepalive interval/count and the
source Netcup deploy script has an output-safe 20-second heartbeat around the
potentially silent migration. This minimizes idle session failures.

The actual stable Netcup script installed in /opt/munshi/bin/ is not byte-for-byte
the source template. A separate audited installation/validation is required;
merging this source branch is NOT equivalent to installing the server patch.
A fully detached server-side runner with durable receipt is the stronger
long-term fix for a true lost SSH connection.

Do not rerun the prior failed GitHub workflow. Do not repeat the 17GB migration
or its backup merely to correct GitHub's status. Apply v0.2.12, server-side
Tsenta consent, and employer receipt evidence remain separate release gates.
