# Phase 6 — Career Preferences and Opportunity Policy

`app.career_policy` is a tenant-scoped, internal-only, feature-gated
decision-support foundation. `MUNSHI_CAREER_POLICY_ENABLED` is off by default.
Migration 020 adds independent preference and AutoPilot-policy records without
modifying legacy Hunter targeting, scoring, dispatch, or product UI.

Preferences and permissions remain structurally separate. The evaluator accepts
only caller-provided, bounded, non-sensitive opportunity facts, returns an
explainable deterministic score and hard-policy reasons, and retains unknowns
as `NEEDS_INPUT`. It rejects protected/self-ID inputs and never imports the
Answer Brain, ranking, resume, n8n, Apply, email, or submission paths.

Autonomy readiness is advisory only: it exposes disabled permissions, missing
facts, and zero/default limits. It cannot submit, email, create accounts, or
grant authority. Rollback is disabling the feature flag; no existing policy is
changed.
