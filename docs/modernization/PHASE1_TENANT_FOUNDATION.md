# Phase 1 tenant foundation

This migration is additive and keeps Hunter in its existing singleton mode. It
creates `tenants`, `app_users`, `tenant_memberships`, and
`owned_record_owners`, then seeds the deterministic `default` / `local-owner`
principal. Existing jobs, operational state, Telegram, n8n queues and callback
records are not reassigned or filtered.

`migrations/015_tenant_foundation.py` applies the schema independently.
Normal `initialize_database()` also installs it, as it does the existing
idempotent Product UI schema helpers. New Product UI lanes and profile facts
write an ownership-registry entry in the same transaction. Retrying a write
does not replace an existing record owner.

The foundation deliberately uses individual SQLite DDL statements instead of
`executescript`, so owner resolution under an enabled future context cannot
commit an enclosing product write. A failed caller transaction rolls back both
the domain write and its ownership entry.

## Flag and future boundary

`MUNSHI_TENANT_FOUNDATION_ENABLED` defaults to off. While off, all ownership
helpers resolve to `default` / `local-owner`; no environment identity is read
and no login or public tenancy route exists. A future authenticated boundary
can set `owner_context(tenant_id=..., user_id=...)` only after explicitly
provisioning the corresponding membership. The helper refuses unknown
memberships.

## Rollback

Disable or omit the flag to retain singleton resolution. The migration creates
only new tables and indexes and does not alter legacy tables, so a code rollback
does not require database rollback. Do not drop the registry from a live
database without a separately reviewed data-retention migration.
