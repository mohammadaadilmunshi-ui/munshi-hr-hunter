# Phase 7 — Relationship Intelligence

`app.relationship_intelligence` adds a disabled-by-default, tenant-scoped
local relationship ledger.  It records only explicitly supplied contact
metadata, source, relevance, confidence, evidence, recommended action, and
optional job linkage.  It performs no enrichment, discovery, outreach, email,
dashboard/UI, n8n, Apply, or submission action.

Contact emails may be stored only with `explicit_contact_email` provenance.
An inferred address pattern is a distinct labelled field and cannot contain an
email value.  Patterns must contain a name placeholder and a valid domain, so
an individual-looking guessed address cannot be relabelled as a pattern.
Owner-scoped information states explicitly distinguish known contacts,
supplied evidence, observed relationships, inferred patterns, and
unknown/unverified values.  Evidence sources and public HTTP(S) URLs are bounded and
validated.  No live finder or paid-enrichment provider is called.

Hunter jobs remain shared.  A relationship link is permitted only where an
upstream authority has already created a matching `owned_record_owners` `job`
association for the current tenant/user.  Reads use composite owner joins, so
another owner cannot discover contacts, evidence, or linkage through a shared
job id.  Rollback is disabling `MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED`; no
existing Hunter authority or contact-finder data is changed.
