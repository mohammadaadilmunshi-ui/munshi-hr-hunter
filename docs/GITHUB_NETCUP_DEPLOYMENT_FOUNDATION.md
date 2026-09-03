# MUNSHI GitHub → Netcup Deployment Foundation V1

## Purpose

Make GitHub the version/deployment authority for MUNSHI source code while Netcup remains the production runtime.

The intended operating model is:

1. Edit from phone / Codex against GitHub.
2. Codex creates a branch or PR.
3. Existing CI plus deployment CI runs on Ubuntu.
4. Review the PR from phone.
5. Merge or select an exact approved commit.
6. Manually run **Netcup Production Deploy** from GitHub Actions.
7. GitHub connects with a restricted deployment SSH credential.
8. Netcup's stable `/opt/munshi/bin/deploy-production-release` wrapper performs a Hunter-only deployment.
9. Health/integrity checks run.
10. On failure the wrapper restores the previous Git SHA and previous Hunter image.

## Critical production invariant

The currently proven production stack is five Compose layers:

- `/opt/munshi/repo/compose.yaml`
- `/opt/munshi/repo/compose.netcup-shadow.yaml`
- `/opt/munshi/runtime/stage10-imported.override.yaml`
- `/opt/munshi/runtime/stage12-production.override.yaml`
- `/opt/munshi/runtime/stage12-n8n-runtime-repair.override.yaml`

The deployment wrapper must never recreate Hunter with fewer layers.

The fifth layer preserves:

- `N8N_USER_FOLDER=/app/n8n-readonly`
- `N8N_DATABASE_PATH=/app/n8n-readonly/database.sqlite`
- `munshi-netcup-shadow_n8n_data:/app/n8n-readonly:ro`

## Deployment safety

Production deployment is intentionally **not** triggered by every push.

The workflow uses `workflow_dispatch` and requires:

- exact 40-character commit SHA
- source branch containing that SHA
- successful deployment CI
- a GitHub `production` environment
- a concurrency group that permits only one production deployment at a time

## Required GitHub secrets

Configure these only after the production deployment user/key is created:

- `NETCUP_HOST`
- `NETCUP_DEPLOY_USER`
- `NETCUP_SSH_PRIVATE_KEY`
- `NETCUP_KNOWN_HOSTS`

Use a pinned `known_hosts` value. Do not use `ssh-keyscan` inside the deployment workflow as the trust bootstrap.

## Recommended GitHub environment

Create an environment named `production`.

If repository protection features are available, restrict deployment branches and optionally add a reviewer. Even without a reviewer, keep the workflow manual and exact-SHA based.

## Stable server-side wrapper

The workflow does not execute arbitrary repository shell over SSH. It invokes:

`/opt/munshi/bin/deploy-production-release`

The server copy should be protected from routine application writes.

The version-controlled source is:

`scripts/netcup/deploy_production_release.sh`

Install/update the stable wrapper only in a separate infrastructure change after CI and review.

## What production deploy is allowed to change

- Git checkout in `/opt/munshi/repo`
- Hunter Docker image
- Hunter container

## What production deploy must not change

- Hunter production database contents
- n8n database contents
- n8n container
- Ollama container
- named production volumes
- secrets
- Mac production authority
- GitHub branch history

## Domain architecture

Initial public domain:

`https://dashboard.munshi.systems`

Caddy terminates TLS and proxies to:

`127.0.0.1:8501`

Do not expose port 8501 publicly.

The included Caddy template uses HTTP Basic Authentication with a hashed password.

Do **not** activate `n8n.munshi.systems` in this first change. n8n has different UI/webhook security considerations and should get its own review.

## Staging

Recommended second phase:

`https://staging-dashboard.munshi.systems`

Do not point staging at the production Hunter database in read-write mode. Build staging with:

- independent Compose project
- production actions disabled
- Telegram disabled
- coordinator/discovery disabled unless specifically being tested
- read-only snapshot or synthetic data

That allows Codex UI changes to be reviewed from a phone before production promotion.

## Current synchronization blocker

At preparation time the proven Netcup repository is ahead of GitHub.

Proven Netcup head:

`7ce1cd33fbe98094cabdd8b9be92f37d75e3e413`

GitHub `feat/cloud-migration-foundation` observed head:

`e932154461ba03a048a346d3e6d487e655cd4c8e`

The proven Netcup SHA is not currently present in GitHub object history.

Do not merge or activate the production deploy path from this preparatory branch until the missing proven commits are synchronized without rewriting history.

After Stage 13 endurance passes:

1. Verify Netcup repo remains clean and at the expected proven head.
2. Synchronize the missing proven commits to GitHub without rewriting history.
3. Verify CI.
4. Rebase/fast-forward this deployment-foundation work onto the synchronized head.
5. Review the deployment diff.
6. Only then configure the production environment/secrets and install the stable server wrapper.

## Phone-first future workflow

Phone / Codex:
- edit code
- open PR
- inspect CI
- review staging
- merge
- open Actions
- run `Netcup Production Deploy`
- paste/select the exact approved SHA
- inspect deployment result

Mac is not required for normal operation.
