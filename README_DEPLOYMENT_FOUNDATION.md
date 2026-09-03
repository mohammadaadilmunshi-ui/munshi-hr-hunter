# MUNSHI GitHub → Netcup Deployment Foundation V1

Prepared during the Stage 13 cloud-migration hardening sequence and reconciled onto the proven Netcup production history.

**DO NOT ACTIVATE AGAINST LIVE NETCUP UNTIL THE DEPLOYMENT KEY, GITHUB PRODUCTION ENVIRONMENT, AND CONTROLLED NON-PRODUCTION/STAGING PROOF ARE COMPLETE.**

This branch remains preparation-only and is intentionally still a draft PR.

Files:
- `.github/workflows/deployment-ci-reusable.yml`
- `.github/workflows/netcup-production-deploy.yml`
- `.github/workflows/repository-safety.yml`
- `.github/workflows/deployment-transport-safety.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `AGENTS.md`
- `deploy/netcup/deploy_production_release.sh`
- `deploy/netcup/verify_production_runtime_contract.sh`
- `deploy/netcup/github_deploy_gateway.sh`
- `deploy/netcup/install_github_deploy_key.sh`
- `deploy/caddy/Caddyfile.dashboard.template`
- `docs/GITHUB_NETCUP_DEPLOYMENT_FOUNDATION.md`

The package preserves the proven five-layer Compose production contract and performs Hunter-only deployment.

The production-only wrappers live under `deploy/netcup/` so the legacy `scripts/netcup` Stage 8B/9 operator-classification contract remains unchanged.

The future GitHub deployment key is restricted through a forced-command gateway, and its installer refuses activation until Stage 13 reports `STATE=PASS`. The server receives only the public key; the private deployment key remains in GitHub Actions secrets.

Current synchronization state:
- Proven Netcup / GitHub migration head: `7ce1cd33fbe98094cabdd8b9be92f37d75e3e413`
- Deployment foundation reconciled onto that proven history: complete
- Reconciled PR CI: Repository Safety Guard, Linux Compatibility, and Docker Foundation passed before transport hardening

Deployment transport hardening:
- GitHub Actions verifies the requested SHA against the requested source branch using its authenticated checkout.
- GitHub Actions creates a Git bundle and streams it over the restricted deployment SSH connection.
- Netcup verifies and imports the bundle locally; production deployment no longer depends on an unauthenticated outbound GitHub fetch.
- The forced-command string is sent without literal shell quotes around the validated SHA/branch values.
- The production repository stays attached to the requested source branch at the exact deployed SHA, preserving later rollback behavior.
- `Deployment Transport Guard` statically verifies these invariants and exercises a real Git-bundle ancestry handoff in CI.

The deployment workflow remains manual, exact-SHA gated, single-concurrency, Hunter-only, and rollback-capable.
