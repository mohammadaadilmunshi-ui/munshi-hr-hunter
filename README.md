# MUNSHI HR Hunter

MUNSHI HR Hunter is the job-discovery, targeting, deduplication, orchestration,
Telegram, dashboard, FastAPI and n8n-backed automation system used by the MUNSHI
job-search platform.

This repository candidate was generated from the verified Mac production source
as part of the cloud-migration program.

## Repository boundaries

This repository contains source code, configuration, migrations, tests and
sanitized n8n workflow source representation.

It intentionally does not contain:

- live Hunter SQLite databases;
- the live n8n database;
- n8n encryption keys;
- provider/API credentials;
- `.env` files;
- logs;
- virtual environments;
- backups;
- rollback copies;
- browser profiles;
- generated evidence.

## Migration rule

Replicate current behavior first. Verify parity. Cut over production authority.
Modernize database/queue architecture afterward.
