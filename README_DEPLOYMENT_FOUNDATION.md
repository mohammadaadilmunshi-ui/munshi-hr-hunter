# MUNSHI GitHub → Netcup Deployment Foundation V1

Prepared while Stage 13 cloud-only endurance is running.

**DO NOT ACTIVATE AGAINST LIVE NETCUP UNTIL STAGE 13 PASSES AND THE PROVEN NETCUP COMMITS ARE SYNCHRONIZED TO GITHUB.**

This branch is intentionally preparation-only.

Files:
- `.github/workflows/deployment-ci-reusable.yml`
- `.github/workflows/netcup-production-deploy.yml`
- `scripts/netcup/deploy_production_release.sh`
- `scripts/netcup/verify_production_runtime_contract.sh`
- `deploy/caddy/Caddyfile.dashboard.template`
- `docs/GITHUB_NETCUP_DEPLOYMENT_FOUNDATION.md`

The package preserves the proven five-layer Compose production contract and performs Hunter-only deployment.

Current known Git synchronization gap:
- Netcup proven head: `7ce1cd33fbe98094cabdd8b9be92f37d75e3e413`
- GitHub migration branch: `e932154461ba03a048a346d3e6d487e655cd4c8e`
- GitHub does not currently contain the proven Netcup SHA.

The deployment workflow is manual, exact-SHA gated, and expected to fail its JobSpy production contract until the missing production-proven history is synchronized.
