# Phase 2 candidate digital-twin foundation

`app/candidate_digital_twin.py` adds an internal-only, tenant-scoped candidate
facts, evidence, preferences, and onboarding store. It is installed additively
by `migrations/016_candidate_digital_twin.py` and normal database
initialization. It has no route, worker, callback, login, credential, Apply,
n8n, or deployment integration.

## Safety and compatibility

`MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED` defaults to off. With it off, the
service rejects reads and writes; the new empty tables are inert. With it on,
the existing Phase 1 owner context selects the tenant/user and enforces the
provisioned membership (tenant overrides additionally require
`MUNSHI_TENANT_FOUNDATION_ENABLED`). Each written fact/preference/onboarding record has a
stable UUID. Facts and preferences retain explicit provenance, numeric
confidence, and a user-confirmation bit. Evidence is attached only to a fact
owned by the current candidate.

The legacy `candidate_profile_facts` contract is not altered, copied, inferred
from, or backfilled. Existing Product UI reads and writes continue unchanged.
A future trusted user flow may explicitly confirm a legacy value and submit it
as a new digital-twin fact.

`internal_profile_payload()` returns the versioned `candidate-profile-v1`
dictionary solely for an in-process trusted adapter. It performs no I/O beyond
the Hunter database. Any external Apply profile sync must remain separately
gated behind a reviewed authenticated bridge and explicit user consent.

## Rollback

Disable or omit `MUNSHI_CANDIDATE_DIGITAL_TWIN_ENABLED` to make the service
inert, then roll back the calling code if needed. Migration 016 only creates
new tables/indexes; it neither changes nor deletes legacy candidate facts. Do
not drop retained candidate evidence without a separately reviewed retention
migration.
