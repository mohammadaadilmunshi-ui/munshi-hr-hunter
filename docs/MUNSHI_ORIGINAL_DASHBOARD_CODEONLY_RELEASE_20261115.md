# Original MUNSHI dashboard — approved code-only release (November 2026)

## Intended change and safety boundary

**Goal:** Publish the already-reviewed manual-job SQLite lock and Relay/Tsenta
status-polling repairs to the original Netcup Hunter dashboard without a
database migration or another multi-GB database backup.

The old production release is
`5d482f75c6ccd001912fbab41146a7f8eb9259ed`.
The approved source branch is
`feat/tsenta-connector-completion-20261015`.

The runtime delta is ONLY:

- `app/manual_input.py` — wait for transient SQLite writers, retain canonical job dedupe
- `app/relay_application_dispatch_v1.py` — isolate failed readback, avoid redundant writes, fair status polling
- `app/relay_receipt_poll_cursor_v1.py` — owner-bound stable-ID cursor with cross-process `flock`
- `app/relay_receipt_worker_v1.py` — bounded receipt worker integration

Tests and incident documentation may also differ. No database/schema modules,
Compose files, Dockerfile, SQL migrations, account policy, or submission authority
may differ from the live baseline. The code-only script explicitly checks its
allowlist before building or replacing Hunter.

**Do not use** the full production deployment simply to publish this patch if
the user has not approved another large backup.

## Already established

- Production Hunter, dashboard, Apply, n8n, and Ollama health were verified.
- Existing online snapshot: `/opt/munshi/backups/hunter-online-20261115T235900Z.db.gz`
  (5,117,573,486 bytes, mode 0600), verified by the online backup tool.
  **Do not make another snapshot for this code-only change.**
- The old source at `/opt/munshi/repo` was clean and on the exact SHA above.
- The original code-only system already exists:
  - `/opt/munshi/bin/deploy-code-only-release`
  - `/opt/munshi/bin/github-deploy-gateway`
  - `.github/workflows/netcup-code-only-deploy.yml`
- The root-owned server scripts are pinned to a *historical* baseline. The
  unprivileged `munshi` account cannot write them and does not have
  passwordless sudo. Respect that boundary.
- Original fix PRs #165 and #166 merged; 99 relevant local tests and 14/14
  workflow checks passed for the original source candidate.

## Root-administrator gate refresh (required, small files only)

This is an **administrator-controlled** step. Never bypass OS permissions via
the Docker group, SSH forced-command tricks, arbitrary scripts, or a secret
credential shared in chat.

1. Review the source delta of `deploy/netcup/deploy_code_only_release.sh`,
   `deploy/netcup/github_deploy_gateway.sh`, and
   `.github/workflows/netcup-code-only-deploy.yml`. They must retain exact-SHA
   verification, the forced gateway grammar, the single allowed production
   branch, the shared GitHub production environment and concurrency lock,
   a strict allowlist, no migration or new DB backup, release idle checks,
   and image rollback.
2. Wait until the code-only gate refresh PR is merged and all GitHub checks
   pass. Verify the exact approved branch HEAD and the two file hashes using
   `git show <approved-sha>:<path> | sha256sum` from a trusted checkout.
3. Using the authorized **root admin session** on Netcup, compare trusted
   candidate files against the reviewed GitHub tree. Keep timestamped small
   copies of the two current root-owned scripts (not the Hunter database).
   Install the reviewed candidates root:root mode 0755 in those same locations.
   Only the small authorization scripts change in this step; Hunter stays
   running. Verify `bash -n` and both installed SHA-256 digests.
4. Do **not** authorize any new SSH command grammar, source branch,
   unrelated file path, or runtime environment override.
5. If the reviewed files cannot be verified or installed through root
   administration, stop: do not attempt a root or Docker permission bypass.

## Protected GitHub deployment

Once both root-owned scripts match the reviewed source, manually dispatch
`netcup-code-only-deploy.yml` from
`feat/tsenta-connector-completion-20261015` using:

- `commit_sha`: exact current approved branch SHA
- `expected_live_sha`:
  `5d482f75c6ccd001912fbab41146a7f8eb9259ed`

The workflow uses the shared `munshi-netcup-production` concurrency group,
the GitHub `production` environment, reusable CI, the branch-head exact-SHA
check, an authenticated Git bundle, and the restricted forced-command gateway.
It is **not** a staging or a full DB-backed migration workflow.

The server script must refuse deployment if live SHA, expected baseline, health,
active submissions, queue idleness, protected container identities, schema
version or data-volume identity fail any guard.

It snapshots the old *Docker image* as a rollback tag, builds and tests the
new image in a no-network container, and recreates **Hunter only**.
It does not execute SQL migrations or create a new database backup.
On failure, it restores the old Git HEAD/image and recreates Hunter.
This is an image rollback, not a database restore.

## Post-release check

- Confirm Hunter API and original Streamlit dashboard are HTTP 200.
- Confirm exact running source/image SHA, unchanged Hunter data volume,
  unchanged SQLite schema version and preserved pre-existing jobs/receipts.
- Confirm n8n, Ollama, Apply/prepare/submit workers retain their original
  container IDs and n8n remains healthy.
- In an explicitly controlled, non-sensitive job-add UI check, verify
  manual save, dedupe and no new n8n dispatch.
- Read existing Tsenta status routes via read-only view, verify the cursor
  advances across older and newer route IDs, no double polling and no new
  SQLite lock bursts; **never** replay actual employer submissions.
- Keep separate historical Tsenta pagination issue #167 open; it is not fixed
  by the receipt-polling release.
- Do not claim production is repaired until all these checks are observed.

If administrator gate refresh is unavailable, leave production unchanged,
report the blocker and keep this verified release as a ready-to-install package.
