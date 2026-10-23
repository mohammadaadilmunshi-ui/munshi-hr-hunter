# Disposable Streamlit acceptance runtime

This runs the **complete application**, not an HTML approximation. Run the
application containers on Netcup staging or a disposable Linux CI runner, never
as a Mac MUNSHI server. Do not run these commands in `/opt/munshi/repo`; use an
independent source archive/staging directory.

The production-derived source baseline is
`53b9907f1f48ceb9a73acec8d6f9d54085fa95a7`. The staging wrapper uses ordinary
renderers, routing, SQLite projections, Streamlit widgets, dialogs and fragments.
It does not mock the frontend or claim synthetic receipts are verified.

## Isolation

One unprivileged dashboard container, internal-only Docker network, read-only
root filesystem, no host/production volumes, no `.env` or production credentials,
and independent tmpfs database/state. No API, workers, n8n, Ollama or Relay service
is started. All operational environment gates are false. An additional Python
audit hook rejects outbound connections, mail and subprocesses; a SQLite
authorizer rejects dispatch, submission, preparation, OAuth and lifecycle writes.
Ordinary **synthetic** job saves, profile edits and preference changes remain
enabled to test working interactions. Production database writes are impossible
because no production database is mounted or configured.

The seed creates fictional jobs and four unverified application states. A new
32-byte encryption key is generated inside this container's tmpfs with mode 0600.
It is never logged, copied, committed, or borrowed from production. Normal AES-GCM
Candidate Truth safeguards remain enabled. Both key and synthetic data disappear
when the disposable container is recreated.

## Build and run (Netcup staging / Linux CI only)

```sh
docker build --tag munshi-uiux-20261019:candidate .
docker build -f deploy/uiux/Dockerfile --tag munshi-uiux-20261019:stage .
docker compose -p munshi-uiux-20261019 -f deploy/uiux/compose.yaml up -d --no-build
docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' munshi-uiux-20261019-dashboard-1
```

Use that private container address through an authorized SSH loopback forward:

```sh
ssh -N -L 127.0.0.1:19511:CONTAINER_PRIVATE_IP:8501 munshi@159.195.244.16
```

Use the existing authorized SSH identity/options. This starts **only a tunnel**
on the Mac. Open `http://127.0.0.1:19511`; the application still runs on Netcup.
The internal Docker network may suppress published-port bindings. Do not disable
network isolation or publish Streamlit publicly to work around that behavior.

## Browser acceptance (browser client only)

Install the repository's locked dependencies and Playwright Chromium/WebKit in a
development test environment. This command does not start an application server:

```sh
python scripts/verify_uiux_runtime.py --url http://127.0.0.1:19511 --output /tmp/munshi-uiux-evidence
```

The script refuses interactions without the synthetic-only runtime marker.
Never target production. The GitHub **Isolated Streamlit Browser Acceptance**
workflow builds the pinned baseline and candidate separately, runs both engines,
then uploads actual screenshots, measurements, interaction results and logs.
That workflow never connects to Netcup or deploys production.

External AI generation, mail, submission and verified receipt reconciliation are
intentionally not executed. Their compatibility is checked by the existing
fixture-driven regression suites, not by making real external calls.
