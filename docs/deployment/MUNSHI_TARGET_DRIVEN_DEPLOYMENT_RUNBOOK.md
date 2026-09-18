# MUNSHI Target-Driven Deployment Runbook

> Purpose: deploy or update MUNSHI targets safely and repeatably without
> hard-coding staging, initial-dashboard, production, server addresses, ports,
> Compose projects, networks, or credentials into reusable deployment code.

## 1. Deployment rule

Reusable deployment code must never choose the target.

Target identity belongs only in an external profile. The same runner should work
for staging, initial-dashboard, and any future explicitly authorized target.

The reusable runner must not contain:
- server IPs or hostnames
- deployment roots
- Compose project names
- published ports
- Docker network names
- protected container names
- target-specific image names
- target-specific internal URLs
- target-specific GitHub environments or secret names

A target profile may contain those values because the profile is the target.

## 2. Safety invariants

Every deployment must fail closed and preserve:
- exact Git SHA pinning
- authenticated source provenance
- persistent database volumes
- database backup before mutation
- rollback to the prior image + checkout
- no main-branch merge as part of deployment
- no production deployment unless explicitly authorized
- protected Compose projects/containers unchanged
- n8n / Ollama / edge services unchanged when they are not part of the target
- production callbacks disabled on non-production targets
- one-use submit authority and final-review safety
- CAPTCHA/MFA/OTP/passkey manual-owner handoff

Never bypass or weaken those invariants to make a deployment pass.

## 3. Correct high-level sequence

### Phase A — local / GitHub preflight

1. Resolve the exact release branch SHA from GitHub.
2. Verify the branch has not moved from the expected SHA.
3. Snapshot the current main SHA.
4. Clone through authenticated GitHub access.
5. Check out the exact release SHA in detached mode.
6. Require a clean working tree.
7. Run the complete source/test gates appropriate to the repo.
8. Build a Git bundle containing the selected release branch.
9. Verify the bundle locally.

### Phase B — server read-only preflight

Before modifying the target:
1. Prove SSH argument transport can safely carry pipes, spaces, quotes, and
   metacharacters.
2. Verify target root, repo, runtime env file, Compose files, Docker network,
   and current service exist.
3. Verify the current service is running/healthy.
4. Verify the current checkout and expected runtime identity.
5. Snapshot preserved services and protected runtime identities.
6. Verify the real persistent data volume.
7. Run database integrity checks.
8. Check stale submit-authority / executable application state when relevant.
9. Verify target runtime dependencies such as n8n, vault, internal HTTP routes,
   and mail routes before live replacement.

### Phase C — authenticated source transfer

1. Copy the exact local Git bundle to the server.
2. Make the bundle explicitly readable by the restricted deploy user.
3. Never depend on root-only temporary-file permissions.

### Phase D — database backup

Before any image rebuild or service recreation:
1. Resolve the canonical database path from the application code.
2. Resolve the real persistent Docker volume.
3. Record database owner/group/mode.
4. Create an SQLite backup using the SQLite backup API.
5. Run `PRAGMA quick_check` on the backup.
6. Record the backup path in the deployment output.

### Phase E — server bundle verification/import

Git bundle verification must have repository context.

Correct pattern:

```bash
runuser -u "$DEPLOY_USER" -- git -C "$REPO" bundle verify "$BUNDLE"
```

Do **not** run:

```bash
git bundle verify "$BUNDLE"
```

outside a Git repository.

Authenticated bundle producers may encode the selected branch as either:
- `refs/heads/<branch>`
- `refs/remotes/origin/<branch>`

The importer may accept those two exact representations only. It must:
1. identify the exact requested branch ref;
2. fetch it to a temporary deploy ref;
3. prove the requested commit exists;
4. prove the requested commit is contained by the bundled branch.

Never guess another ref.

### Phase F — effective Compose safety

Before build/recreate:
1. render the complete effective Compose configuration;
2. verify non-production isolation flags;
3. verify the requested optional lanes;
4. verify the intended target network/project values came from the target
   profile;
5. stop before mutation if the rendered contract is wrong.

For non-production targets, explicitly verify at least:
- shadow/non-production mode is enabled where applicable
- production state is not imported
- production callbacks are disabled

### Phase G — deploy only the intended service

1. Update the target checkout to the exact SHA.
2. Confirm the working tree is clean.
3. Compile/source-check the runtime.
4. Build only the service that changed.
5. Recreate only that service.
6. Do not use `docker compose down`.
7. Do not delete volumes.
8. Do not recreate n8n, Ollama, edge, production, or unrelated services.

### Phase H — post-deploy proof

Require:
- target service running
- health check healthy
- HTTP health endpoint passes
- exact live SHA equals intended SHA
- `PRAGMA quick_check` passes
- `PRAGMA foreign_key_check` returns no violations
- secure vault available
- expected supervised workers are running
- restart counts remain zero where expected
- preserved-service snapshots match
- protected-runtime snapshots match
- main SHA unchanged
- production deployment = NO

For Apply, also verify:
- source-derived migration count and exact latest migration
- prepare worker running
- submit worker running
- Hunter ↔ Apply connectivity
- stale submit-authority guard
- final review / submit gates exactly as intended for the target
- verified receipt path

## 4. Known failures and permanent lessons

### Failure: target profile executed as shell

Symptom:
- pipe-delimited Compose values were interpreted as shell pipelines.

Cause:
- using `source "$PROFILE"`.

Permanent rule:
- parse target profiles as literal `KEY=value` data.
- never `source` or `eval` a deployment profile.

### Failure: Bash 3.2 incompatibility on macOS

Symptoms:
- `declare -A: invalid option`
- later discovery of `local -n`.

Cause:
- assuming modern Homebrew Bash.

Permanent rule:
- reusable local runners must remain compatible with Apple Bash 3.2 unless a
  newer Bash version is an explicit prerequisite.
- no associative arrays, namerefs, `mapfile`, `readarray`, or other
  Bash-4-only constructs.

### Failure: incorrect tilde expansion

Symptom:
- SSH key resolved as `/Users/.../~/.ssh/...`.

Cause:
- incorrect literal-profile expansion.

Permanent rule:
- expand only a leading literal `~/` explicitly and safely.

### Failure: staging-only HTTP hooks survived target refactor

Symptoms:
- stale imports of `STAGING_HTTP_ENV`
- stale `allow_staging_http`
- hard-coded `http://hunter:8000`.

Permanent rule:
- one target-neutral internal-HTTP policy.
- exact base URL must come from target runtime configuration.
- production may never use a plaintext internal-HTTP exception.
- static deployment guard must scan all hosted prepare/submit/trust/artifact,
  receipt, mail, and authority clients.

### Failure: unused import after refactor

Symptom:
- Ruff stopped deployment on a stale import.

Permanent rule:
- run Ruff and full native-host tests before server mutation.

### Failure: authenticated bundle branch ref mismatch

Symptom:
- bundle contained `refs/heads/<branch>` while deploy importer expected only
  `refs/remotes/origin/<branch>`.

Permanent rule:
- accept only those two authenticated exact branch representations.
- always prove requested SHA ancestry against the selected bundle ref.

### Failure: unsafe SSH argument transport

Symptoms:
- remote shell tried to execute Compose filenames and migration filenames
  because values containing `|` were reparsed.

Cause:
- `ssh host bash -s -- "$@"` does not preserve arbitrary argument values
  through remote-shell reconstruction.

Permanent rule:
- shell-escape every remote argument before building the remote command.
- run a transport self-test with pipes, spaces, dollar signs, single quotes,
  and double quotes before the deployment phases.

### Failure: container heredoc without stdin

Risk:
- `docker exec ... python - <<PY` may not deliver the heredoc to the
  container process.

Permanent rule:
- use `docker exec -i ... python - <<PY`.
- static guard the deployment runner for this.

### Failure: hard-coded migration version

Risk:
- a later release fails because the runner expects one historical migration
  filename/count.

Permanent rule:
- derive expected migration filenames/count/latest schema from the exact
  checked-out release.
- prove the deployed database migration set equals the target release after
  migration.

### Failure: trust-aware preparation existed but was not wired

Risk:
- UI claimed security checkpoint support while Compose launched the older
  prepare worker.

Permanent rule:
- hosted preparation must launch the trust-aware worker when the product
  promises CAPTCHA/MFA/security checkpoint persistence.
- protect this with a static deployment guard.

### Failure: generic network had a staging-only internal key

Risk:
- reusable Compose retained environment semantics even though external network
  name was profile-driven.

Permanent rule:
- internal Compose keys should also be target-neutral (for example,
  `hunter_internal`).

### Failure: `git bundle verify` without repository context

Symptom:
- `error: need a repository to verify a bundle`.

Cause:
- server-side runner executed `git bundle verify "$BUNDLE"` outside the
  target Git repository.

Permanent rule:
- use:
  `runuser -u "$DEPLOY_USER" -- git -C "$REPO" bundle verify "$BUNDLE"`.

### Failure risk: root-owned transferred bundle

Risk:
- restricted deploy user may not be able to verify/list/fetch the transferred
  bundle.

Permanent rule:
- after transfer, explicitly set owner/readability for the deploy user and
  prove `test -r` under that user before deployment.

### Failure risk: rollback through obsolete container ID

Risk:
- after recreation, the pre-deploy container ID no longer safely represents the
  live data mount.

Permanent rule:
- resolve the persistent data volume before recreation.
- restore the database directly through that volume on rollback.
- restore owner/group/mode and remove stale WAL/SHM/journal files.
- run quick-check and foreign-key-check after restore.

## 5. Integration deployment lessons

Telegram and SMTP credentials must not be hard-coded into Git or target
profiles.

Preferred model:
- environment variable remains authoritative when present;
- otherwise read the credential from the encrypted server-side vault;
- UI is write-only for secrets;
- stored plaintext is never rendered back to the browser.

Notification workers may remain supervised only when:
- they cannot create submit authority;
- they cannot bypass the final review gate;
- delivery remains off unless valid delivery credentials and explicit user
  notification preferences exist.

Cloudflare inbound mail is **not** end-to-end ready merely because Hunter has a
signing secret.

It is ready only after:
1. the same signing secret is installed on the Worker;
2. the public Worker endpoint can reach Hunter;
3. a real signed POST is accepted;
4. a real inbound email reaches the intended user/application;
5. receiving mail cannot trigger application submission.

## 6. Staging → initial-dashboard rule

Do not rewrite the deployment runner for initial-dashboard.

Instead:
1. freeze a tested release SHA;
2. create a separate `initial-dashboard` target profile;
3. fill only that target's root/project/ports/network/image/internal URLs and
   protection identities;
4. run the same reusable runner;
5. require the same preflight, backup, rollback, isolation, health, DB, and
   post-deploy proofs.

If source code needs an `if staging ...` / `if initial-dashboard ...`
deployment branch, the design is wrong. Target identity belongs in the profile.

## 7. Final deployment acceptance checklist

A deployment is not complete until all are true:

- [ ] release SHA resolved from GitHub and unchanged
- [ ] main SHA snapshotted
- [ ] source/test/CI gates green
- [ ] literal target-profile parser used
- [ ] SSH transport probe passed
- [ ] exact authenticated checkout clean
- [ ] local bundle verified
- [ ] server bundle readable by deploy user
- [ ] server bundle verified with repository context
- [ ] requested SHA contained by exact bundled branch
- [ ] target root/env/Compose/network preflight passed
- [ ] persistent data volume identified
- [ ] DB backup created + quick-check passed
- [ ] stale submit/executable state guard passed where relevant
- [ ] effective Compose safety render passed
- [ ] only intended service(s) recreated
- [ ] target service healthy
- [ ] exact live SHA verified
- [ ] post-deploy DB quick-check passed
- [ ] post-deploy foreign-key-check passed
- [ ] expected worker processes running
- [ ] preserved services unchanged
- [ ] protected runtime unchanged
- [ ] main SHA unchanged
- [ ] production deployment not performed
- [ ] deployment report written
- [ ] optional external integrations reported separately as
      configured/proven/unproven

## 8. Current successful Hunter staging proof (2026-09-18)

The successful Hunter target update proved:
- release SHA `816e477590effdeeb6136a09aa75ce54aa2fedaa`
- local source gates passed
- server read-only target/protection preflight passed
- transferred bundle readable by restricted deploy user
- server bundle verification passed
- effective Compose safety passed
- Hunter image built and Hunter container recreated
- post-deploy DB quick-check passed
- post-deploy foreign-key-check passed
- secure vault ready
- Hunter integration-readiness endpoint loaded
- preserved target services unchanged
- protected runtime unchanged
- production deployment = NO
- main branch unchanged
- final result = `MUNSHI_HUNTER_TARGET_UPDATE_PASS`

At that point the integration readiness was:
- Telegram: not configured
- SMTP2GO: not configured
- Hunter mail signing secret: configured
- external mail-secret sync: not yet confirmed

That distinction must remain visible in future deployment reports.
