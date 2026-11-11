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
7. GitHub verifies that the exact SHA belongs to the requested source branch using its authenticated checkout.
8. GitHub creates a Git bundle containing that authenticated branch history.
9. GitHub streams the bundle over a restricted deployment SSH credential.
10. Netcup's forced-command gateway validates the exact SHA/branch command shape.
11. A stable `/opt/munshi/bin/deploy-production-release` wrapper verifies/imports the streamed bundle locally and performs a Hunter-only deployment.
12. A stable `/opt/munshi/bin/verify-production-runtime-contract` verifier checks the live environment before and after deployment.
13. On failure the wrapper restores the previous Git SHA/branch state and previous Hunter image.

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
- valid Git source branch containing that SHA
- successful deployment CI
- a GitHub `production` environment
- a concurrency group that permits only one production deployment at a time

The Netcup production host does **not** need a GitHub PAT, personal GitHub credential, or outbound authenticated Git remote for deployment. The approved source history is transported as a Git bundle over the same restricted SSH channel used to invoke deployment.

This avoids the anonymous HTTPS-fetch failure mode previously observed from Netcup while preserving exact Git commit objects and ancestry verification.

## Required GitHub secrets

Configure these only after the production deployment key is created and the server-side gateway is approved:

- `NETCUP_HOST`
- `NETCUP_DEPLOY_USER`
- `NETCUP_SSH_PRIVATE_KEY`
- `NETCUP_KNOWN_HOSTS`

Use a pinned `known_hosts` value. Do not use `ssh-keyscan` inside the deployment workflow as the trust bootstrap.

## Recommended GitHub environment

Create an environment named `production`.

If repository protection features are available, restrict deployment branches and optionally add a reviewer. Even without a reviewer, keep the workflow manual and exact-SHA based.

## Stable server-side wrappers

The GitHub workflow does not get an unrestricted shell on Netcup.

A GitHub-specific SSH public key is installed with a forced command:

`/opt/munshi/bin/github-deploy-gateway`

The gateway ignores arbitrary shell input and accepts only the exact command shape:

`/opt/munshi/bin/deploy-production-release --commit <40-char-sha> --branch <approved-branch>`

The workflow constructs that command from values already validated by the workflow and sends it without literal quote characters around the SHA or branch, so the server-side forced-command regex receives the canonical form it expects.

The live deployment then uses two stable root-owned/protected copies:

- `/opt/munshi/bin/deploy-production-release`
- `/opt/munshi/bin/verify-production-runtime-contract`

The version-controlled sources are deliberately outside the legacy `scripts/netcup` operator directory:

- `deploy/netcup/deploy_production_release.sh`
- `deploy/netcup/verify_production_runtime_contract.sh`
- `deploy/netcup/github_deploy_gateway.sh`
- `deploy/netcup/install_github_deploy_key.sh`

The installer refuses to activate the deploy key unless the Stage 13 server-side endurance status contains `STATE=PASS`.

The installer only consumes a public SSH key. The private GitHub Actions deployment key is never copied to Netcup.

## Git bundle deployment transport

GitHub Actions performs the source-side trust work:

1. Check out the exact requested SHA.
2. Fetch the requested source branch using the workflow's authenticated GitHub checkout credentials.
3. Prove the requested SHA is an ancestor of that branch.
4. Create a Git bundle from the authenticated branch ref.
5. Verify the bundle locally.
6. Stream the bundle through stdin over the restricted SSH deployment connection.

Netcup then:

1. Saves the streamed bundle to a temporary file.
2. Verifies the bundle with `git bundle verify`.
3. Imports it into a temporary deployment-only remote ref.
4. Proves the requested SHA is contained in the bundled branch history.
5. Checks out the exact SHA on the requested local source branch, rather than leaving production in detached-HEAD state.
6. Removes the temporary bundle/deployment ref on exit.

No `git fetch origin` is required on Netcup during deployment.

`Deployment Transport Guard` protects this contract on every PR. It checks the workflow/wrapper/gateway tokens, rejects the earlier quoted-command pattern and outbound Netcup fetch dependency, validates deployment shell syntax, and exercises a real Git-bundle ancestry import in temporary Git repositories.

## What production deploy is allowed to change

- Git checkout/ref in `/opt/munshi/repo` to the exact approved SHA
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

Recommended next phase:

`https://staging-dashboard.munshi.systems`

Do not point staging at the production Hunter database in read-write mode. Build staging with:

- independent Compose project
- production actions disabled
- Telegram disabled
- coordinator/discovery disabled unless specifically being tested
- read-only snapshot or synthetic data

That allows Codex/UI/runtime deployment changes to be reviewed from a phone before production promotion.

## Current migration/deployment state

The earlier GitHub ↔ Netcup synchronization blocker is resolved.

- Proven Netcup head: `7ce1cd33fbe98094cabdd8b9be92f37d75e3e413`
- GitHub `feat/cloud-migration-foundation`: synchronized to the same exact SHA
- Deployment foundation branch: reconciled onto the proven history without force-push
- Stage 13 cloud-only endurance: PASS
- Controlled Netcup reboot/recovery: PASS with one recovered transient startup SQLite contention observation
- Reconciled CI before transport hardening: Repository Safety Guard PASS, Linux Compatibility PASS, Docker Foundation PASS
- Deployment transport hardening: implemented in PR #2; hardened CI must be green at the final PR head before activation

The deployment path remains **not activated**. Remaining activation gates are:

1. Complete CI for the deployment-transport hardening patch.
2. Review the final deployment diff.
3. Create a dedicated GitHub Actions deploy keypair.
4. Install only its public key through the Stage13-gated forced-command installer.
5. Configure the GitHub `production` environment and secrets.
6. Perform a controlled non-production/staging deployment proof.
7. Only then consider the first GitHub-driven production promotion.

## Phone-first future workflow

Phone / Codex:
- edit code
- open PR
- inspect CI
- review staging
- merge/select approved source SHA
- open Actions
- run `Netcup Production Deploy`
- paste/select the exact approved SHA and source branch
- inspect deployment result

Mac is not required for normal operation.
