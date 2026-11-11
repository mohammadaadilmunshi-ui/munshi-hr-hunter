# Stage 9 Netcup Endurance

- Timestamp: 2026-08-31 (America/New_York)
- Host: pending explicit input
- Architecture target: x86_64
- n8n target: 2.22.5
- Production Mac mutation count: `0`

`scripts/netcup/endurance_watch.sh` supports 1, 6, 24, 48, and 72 hour runs. Each interval records container health and restart counts, host load, RAM/swap, free disk, Docker usage, zombie and Chromium process counts, SQLite integrity, kernel/OOM signals, n8n crash/segfault signals, and bounded-log growth. Intentional Ollama CPU saturation is not itself a failure; health recovery, memory safety, responsiveness, and absence of OOM are decisive.

Automatic NO-GO events include an OOM kill, unexplained restart, missing container, three consecutive health failures, n8n fatal/crash evidence, relevant kernel/filesystem errors, database integrity failure, less than 10 GiB free disk, zombies, or log growth above 1 GiB total or a sustained 512 MiB/hour. The watcher never hides events. `scripts/netcup/endurance_report.sh` summarizes samples and preserves the raw evidence directory.

Milestones proceed 1 -> 6 -> 24 -> 48 -> 72 hours when the host remains available. Any patch that materially changes runtime behavior restarts the applicable milestone. Final `GO_STAGE9_CLOUD_SHADOW_PROVEN` requires initial parity, reboot recovery, and the required endurance evidence; it does not authorize state migration or cutover.
