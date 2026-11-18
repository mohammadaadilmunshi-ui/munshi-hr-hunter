# One-time MUNSHI crown/theme release — no database backup

**Reviewed production baseline:** `31c6c5b7c2942b7c0416ce1dc2f0dd11898f8d5a`
**Approved application image target:** `b910c4976f3a3c81f57cb89950a6314cdc6b8048`
**Branch:** `feat/tsenta-connector-completion-20261015`
**Scope:** favicon, Apple/PWA app icons, and system-aware loader, exactly 20 changed files.

## Invariants

- No database backups, WAL checkpoints, data snapshots, migrations, database copies, or schema edits.
- The only reviewed Dockerfile change adds `COPY static ./static`; `.streamlit/config.toml` enables curated static files.
- Git SHA and release branch are pinned. Any other file delta or changed live SHA fails closed.
- A new Docker **image** can be built; the prior image is retained as a tag for rollback.
- Only the Hunter dashboard service is recreated. n8n, Ollama, Apply and submit workers must keep their existing container IDs.
- No public dashboard data routes are opened. Only the eight exact static brand URLs bypass login.

## Why administrative setup is required

The existing root-owned `/opt/munshi/bin/deploy-code-only-release` and SSH forced gateway are pinned to **another** prior release. The `munshi` user does **not** have sudo permission to install or replace these security controls. Do **not** bypass this limit through Docker privilege tricks, a custom remote shell execution, or the full database-backed release.

This PR adds a separate one-time, checksum-pinned code-only script, a single exact SSH gateway command, and a `crown-20261117` mode to the existing protected GitHub workflow. The legacy deployment route is unchanged.

## Activation by authorized Netcup administrator only

Once this PR is reviewed and merged into the production release branch, the unprivileged `munshi` account may stage **only** these files from the approved branch under `/opt/munshi/runtime/crown-no-backup-20261117/`:

- `deploy_crown_no_backup_release_v1.sh`
- `github_deploy_gateway.sh`
- `install_crown_no_backup_gate_v1.sh`

The administrator must verify the reviewed Git commit and their SHA-256 checksums before running the installer from an authenticated **Netcup root console**. Do not provide passwords or root SSH keys to a chat.

The administrator must verify the installer’s exact checksum **before** running the staged script, because the staging directory is owned by the unprivileged deploy account. From the authenticated Netcup root console:

```bash
printf '%s  %s\n' \
  '807eaeb32197e4311a524f670448186cb1a77945423f5a63ee83afbacbcad6b5' \
  '/opt/munshi/runtime/crown-no-backup-20261117/install_crown_no_backup_gate_v1.sh' \
  | sha256sum -c --status && \
bash /opt/munshi/runtime/crown-no-backup-20261117/install_crown_no_backup_gate_v1.sh
```

The installer uses Git’s **command-scoped, exact-path** `safe.directory` option when root reads the `munshi`-owned repository. Do not add a wildcard or global Git trust exception.

The installer refuses altered files and unexpected baseline, insists on a healthy live Hunter, and changes only two small root-owned deployment gate files. It does not restart the app. It retains a roughly 2 KB copy of the prior SSH gateway so this control change can be reversed; **it does not back up or read the 26.6 GB database**.

## Approved GitHub deployment trigger

After the root-owned one-time gate is confirmed installed, run the existing *Netcup Hunter Code-Only Deploy* GitHub workflow on the protected production branch using:

```bash
gh workflow run netcup-code-only-deploy.yml \
  --repo mohammadaadilmunshi-ui/munshi-hr-hunter \
  --ref feat/tsenta-connector-completion-20261015 \
  -f release_kind=crown-20261117 \
  -f commit_sha=b910c4976f3a3c81f57cb89950a6314cdc6b8048 \
  -f expected_live_sha=31c6c5b7c2942b7c0416ce1dc2f0dd11898f8d5a
```

The workflow runs the production CI, checks the exact SHA, builds an authenticated Git bundle, and calls the newly approved **single exact** forced SSH command. The remote script refuses busy application submissions and n8n queues, performs read-only database schema/version verification, builds a new image, runs isolated tests with no network and ephemeral test data, switches only the Hunter image, and checks health. No full DB backup/migration step exists in this path.

## Post-deployment acceptance

Verify that:

- The live dashboard Git HEAD is exactly `b910c4976f3a3c81f57cb89950a6314cdc6b8048`.
- Hunter, API and Streamlit are healthy; n8n/Ollama/Apply container IDs are unchanged.
- Hunter DB volume identity and schema version are unchanged.
- `/app/static/apple-touch-icon.png`, dark/light favicon files, and manifest are HTTP 200 and image/JSON types respectively without login.
- Dashboard application pages still require a login without a device token.
- The opening screen follows the device appearance while preserving any manually selected website theme.

**Stop immediately** if any guard fails. Keep the existing image as the only rollback artifact. Do not run the full production deployment workflow for this UI change.
