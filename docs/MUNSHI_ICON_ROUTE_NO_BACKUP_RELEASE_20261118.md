# One-time Netcup icon-route hotfix, no database backup

**Previously deployed app:** `b910c4976f3a3c81f57cb89950a6314cdc6b8048`
**Exactly reviewed icon 404 fix:** `7998acf6f89a3f9dd00d574f14777efa31772ab3`
**Protected branch:** `feat/tsenta-connector-completion-20261015`

The original crown/theme release succeeded, but the browser requests for `/app/static/*` returned 404: the edge routes to FastAPI, not Streamlit. This hotfix registers a FastAPI route that serves only eight existing source-controlled icon/manifest files. Existing dashboard access rules remain intact.

## No-backup guarantee

- No copy, backup, snapshot, or migration of the ~26.6 GB SQLite database.
- No edits to schema, Compose, Dockerfile, or existing icon artwork.
- Preserve the old Hunter Docker image tag; retain the existing database volume.
- Check SQLite schema number and volume identity before and after, and verify unchanged n8n, Ollama, Apply and submission worker identities.
- Abort if app submissions or the n8n dispatch queue are active.
- Hotfix source is exact-SHA pinned, allowlisting exactly nine reviewed files (including previous release-gate documentation and tests). Changed application logic is limited to the FastAPI icon route.
- Never execute the old full-backup production workflow for this update.

## Review and installation

The server's SSH gateway is root-owned. The unprivileged `munshi` account cannot install the reviewed gate and **must not attempt Docker-group privilege elevation or unapproved remote execution**.

The administrator may verify the tiny staged files at `/opt/munshi/runtime/icon-route-20261118/` using these fixed hashes:

- Release script: `b6ad6b95acd3bd45228e08e8be8f6d0053df08f6fe2ee661a4955e03f34b450c`
- Exact-match gateway: `d5264b5ff54eb3eb4d7f21c7b169d03415db44e6e80b8841b9c2e5db7d7d8c59`
- Root installer: `c1202d0e5675748842bf4f36f0072b5475315c99be6999b16721088f7271f1c0`

After independently reviewing the PR and confirming checksums, run at an **authorized Netcup root console**, not under the restricted deploy user:

```bash
cd /opt/munshi/runtime/icon-route-20261118
sha256sum install_icon_route_no_backup_gate_v1.sh
```

Only if the printed hash matches the exact value above, run:

```bash
bash install_icon_route_no_backup_gate_v1.sh
```

It must print `ICON_ROUTE_GATE_INSTALLED=YES`, `DATABASE_BACKUP=NONE`, `DATABASE_MIGRATION=NONE`, `APP_NOT_RESTARTED=YES`. The installer changes two small root-owned deployment controls, retains a tiny previous gateway file for rollback, and **does not** restart the app.

## Protected exact-SHA deployment

After the application PR and this deployment-gate PR merge, and the administrator installation is verified, use GitHub's preexisting protected *Netcup Hunter Code-Only Deploy* workflow:

```bash
gh workflow run netcup-code-only-deploy.yml \
  --repo mohammadaadilmunshi-ui/munshi-hr-hunter \
  --ref feat/tsenta-connector-completion-20261015 \
  -f release_kind=icon-route-20261118 \
  -f commit_sha=7998acf6f89a3f9dd00d574f14777efa31772ab3 \
  -f expected_live_sha=b910c4976f3a3c81f57cb89950a6314cdc6b8048
```

The workflow runs production CI and only then passes a pinned Git bundle to one exact permitted SSH command. The server validates source diff, health, active queue and unchanged schema, tests the new image offline with synthetic data, then recreates Hunter alone.

## Acceptance

- Git HEAD equals `7998acf6f89a3f9dd00d574f14777efa31772ab3`, Hunter healthy.
- All eight `https://dashboard.munshi.systems/app/static/{filename}` routes answer HTTP 200 with correct MIME and binary data, without needing a login token.
- Requests to `/` still redirect unauthenticated visitors to the enrollment/login flow; no private paths become public.
- Original crown and system-aware loading screen remain unchanged.
- No database backup files created, same database volume/schema and unchanged n8n/Ollama/Apply containers.

If any guard fails, stop and inspect before retrying; the prior Hunter image is the only rollback artifact.
