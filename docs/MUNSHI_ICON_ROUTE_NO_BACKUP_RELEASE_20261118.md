# One-time Netcup icon-route hotfix, no database backup

**Previously deployed app:** `b910c4976f3a3c81f57cb89950a6314cdc6b8048`
**Exactly reviewed icon 404 fix:** `854fcc01bc80f3a209195274d6e6d099c02d8e66`
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

- Release script: `91e485f154c8d89654c2fed6243349055e1ee2079e4253d5260741a4034e40c0`
- Exact-match gateway: `841935f53dc4c6f6ca9a65eb75ff189d2d4a691c3bfca004e9d787cda1361b83`
- Root installer: `3cb64e5efe88948c7c462f12b1b21bb20cfe11c617b8b63f6ebd09fedc449591`

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
  -f commit_sha=854fcc01bc80f3a209195274d6e6d099c02d8e66 \
  -f expected_live_sha=b910c4976f3a3c81f57cb89950a6314cdc6b8048
```

The workflow runs production CI and only then passes a pinned Git bundle to one exact permitted SSH command. The server validates source diff, health, active queue and unchanged schema, tests the new image offline with synthetic data, then recreates Hunter alone.

## Acceptance

- Git HEAD equals `854fcc01bc80f3a209195274d6e6d099c02d8e66`, Hunter healthy.
- All eight `https://dashboard.munshi.systems/app/static/{filename}` routes answer HTTP 200 with correct MIME and binary data, without needing a login token.
- Requests to `/` still redirect unauthenticated visitors to the enrollment/login flow; no private paths become public.
- Original crown and system-aware loading screen remain unchanged.
- No database backup files created, same database volume/schema and unchanged n8n/Ollama/Apply containers.

If any guard fails, stop and inspect before retrying; the prior Hunter image is the only rollback artifact.
