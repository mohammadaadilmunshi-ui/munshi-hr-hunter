# MUNSHI GitHub → Netcup Deployment Foundation V1

Prepared while Stage 13 cloud-only endurance is running.

**DO NOT ACTIVATE AGAINST LIVE NETCUP UNTIL STAGE 13 PASSES AND THE PROVEN NETCUP COMMITS ARE SYNCHRONIZED TO GITHUB.**

This branch is intentionally preparation-only.

Files:
- `.github/workflows/deployment-ci-reusable.yml`
- `.github/workflows/netcup-production-deploy.yml`
- `.github/workflows/repository-safety.yml`
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

Current known Git synchronization gap:
- Netcup proven head: `7ce1cd33fbe98094cabdd8b9be92f37d75e3e413`
- GitHub migration branch: `e932154461ba03a048a346d3e6d487e655cd4c8e`
- GitHub does not currently contain the proven Netcup SHA.

The deployment workflow is manual, exact-SHA gated, and expected to fail its JobSpy production contract until the missing production-proven history is synchronized.
