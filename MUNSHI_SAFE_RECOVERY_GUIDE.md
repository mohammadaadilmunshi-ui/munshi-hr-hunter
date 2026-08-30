# MUNSHI Apply Safe Recovery Guide

## Normal use

Nothing is required. MUNSHI Apply's canonical macOS LaunchAgents restore the core runtime automatically after login following a restart or shutdown. They do not depend on an open Terminal or Codex session.

Sleep and temporary network loss do not reset schedules or queues. On wake or reconnection, the existing randomized scheduler continues through its single serialized worker lane; missed providers are not launched in a burst.

## If something looks wrong

```bash
cd ~/Aadil-HR-Hunter
./bin/munshi-safe-restart status
```

`status` is read only. It reports launchd ownership, process identity, ports, Telegram heartbeat, scheduler state, source-worker ownership, network availability, and SQLite quick checks.

## Recover missing services

```bash
./bin/munshi-safe-restart recover
```

`recover` is the preferred command. It preserves healthy services and starts or repairs only components that are genuinely missing or unhealthy. Repeating it is safe and idempotent.

## Full controlled restart

```bash
./bin/munshi-safe-restart restart
```

`restart` performs one dependency-aware cycle. It waits for an active source worker, refuses to interrupt active n8n executions, uses graceful termination, verifies startup health, and compares durable state before and after. It never resets source schedules, targeting, credentials, queues, delivery claims, or history.

## Verify

```bash
./bin/munshi-safe-restart verify
```

`verify` is read only and exits nonzero if a canonical LaunchAgent is missing, a core service is unhealthy, a singleton is duplicated, a source lock is stale, or a database quick check requires attention.

## Start and stop

```bash
./bin/munshi-safe-restart start
./bin/munshi-safe-restart stop
```

`start` only starts stopped components. `stop` is a controlled operational stop and refuses unsafe interruption. For ordinary incidents, use `recover` instead.

## Safety behavior

- Exactly one canonical owner exists for n8n, FastAPI, Telegram, Streamlit, the randomized source scheduler, and the hourly coordinator.
- The randomized scheduler launches at most one due provider per cycle and preserves jitter, cooldown, and failure backoff.
- Source-worker locks are removed only after PID, process command, and process-start identity prove the owner is gone.
- SQLite WAL/SHM files are never deleted. A transient BUSY state is retried and reported as an active writer.
- Telegram claims and n8n idempotency/queue state are never replayed or cleared by recovery.
- Durable adapter-run cards remain in the operational outbox across restarts. Recovery retries only cards whose canonical delivery state is pending/retrying; it never regenerates or replays historical summaries.
- Core restart loops are throttled by launchd; failed manual recoveries enter bounded backoff.
- Network unavailability is reported separately and never causes every provider to run or every service to restart.

The same safe `recover` path is available in **System / Diagnostics → Runtime recovery**.
