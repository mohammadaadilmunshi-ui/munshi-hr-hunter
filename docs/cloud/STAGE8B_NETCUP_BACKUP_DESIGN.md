# Stage 8B Netcup Backup Design

- Timestamp: 2026-08-31 (America/New_York)
- Preparation baseline HEAD: `274feb4dcd1cdf780e49cf8c1b8aede168c6b584`
- Branch: `feat/cloud-migration-foundation`
- Host/OS/architecture/Docker/Compose: pending Netcup provisioning
- n8n target: `2.22.5`
- Canonical workflow SHA-256: `501f144f35c5ae514a2c96004763232014ae78fa3a266f33a582f780cb22534f`
- Container/test/Git status: preparation validation recorded by the Stage 8B operator
- Safety status: design only; production state access forbidden
- Production Mac mutation count: `0`

This is design only. No production database, n8n state, encryption key, credential, or runtime artifact is copied during Stage 8B/9.

## Backup units

- Hunter: use SQLite's online backup API or `sqlite3 hunter.db ".backup 'snapshot.db'"`, then run `PRAGMA integrity_check` against the snapshot and record SHA-256, size, timestamp, schema version, and source commit.
- n8n: stop only the future cloud n8n writer at a controlled boundary or use SQLite `.backup` with WAL-aware consistency. Back up `database.sqlite` plus the separately managed n8n encryption key. A database without the matching key is not a usable backup of encrypted credentials.
- Volumes: archive only after application-consistent database snapshots are present. Record Docker volume labels, owners, modes, and image versions. Ollama models may be re-pulled and checksummed rather than retained in every backup.
- Secrets: encrypt separately with a human-controlled recipient. Never place plaintext secrets in Git, ordinary reports, shell history, or shared archives.

## Destination and retention

Stage 8B creates only `/opt/munshi/backups` as a local staging area. Later approved options, without purchasing new infrastructure, are an encrypted copy to an existing human-controlled destination or an encrypted offline download. Recommended retention after cutover is 7 daily, 4 weekly, and 6 monthly restore points, subject to verified available capacity. Local-only backups do not protect against host loss and are not sufficient disaster recovery.

## Restore and proof

Restore into new, empty shadow volumes; set exact ownership and 0600 secret/database permissions; verify SHA-256 before opening databases; run SQLite integrity checks; start services with Telegram/discovery/schedulers/callback authority disabled; verify n8n can decrypt only a synthetic credential; run the full shadow validator; and record a disaster-recovery report. Production authority must not change during a restore drill. Quarterly restore drills are recommended after cutover authorization.

Production Mac mutation count: `0`.
