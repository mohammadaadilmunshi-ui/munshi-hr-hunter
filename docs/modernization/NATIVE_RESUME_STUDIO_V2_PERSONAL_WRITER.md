# MUNSHI Native Resume Studio V2 — Personal Writer Controls

## Scope

This change strengthens the existing preparation-only Native Resume Studio without changing application authority or production deployment behavior.

V2 adds:

- candidate-confirmed Master Resume source upload/review flow;
- explicit JD rewrite strengths: Slight, Medium, Aggressive;
- candidate-managed encrypted OpenAI API key support;
- per-candidate model, reasoning, max-output-token, and max-API-call controls;
- immutable version diagnostics that record rewrite mode and non-secret writer metadata;
- stronger UI explanation of truth, cost, and n8n authority boundaries.

## Authority contract

Unchanged:

- n8n remains the authoritative application-preparation path;
- Native Resume Studio remains preparation-only;
- `native_resume_authority_enabled()` remains `False`;
- no application is submitted or marked Submitted by this feature;
- no Master Resume application designation is silently replaced.

## Master Resume source

The user may upload a DOCX/TXT/Markdown source or paste truthful resume text. The extracted text is shown for review before it is saved as the active Resume Studio evidence source.

A confirmation checkbox is required before saving. Existing evidence filtering still excludes sensitive self-identification data from the writer payload.

PDF source parsing is deliberately not added in this change because the current runtime has no locked PDF text-extraction dependency. It can be added as a separate, tested dependency change later.

## Rewrite strengths

### Slight

Preserves structure, ordering, and most language. Makes targeted JD-specific edits to summary, supported skills ordering, and directly relevant bullets.

### Medium

Balanced rewrite. Reworks summary and bullet language, reprioritizes supported evidence, and condenses lower-relevance content while preserving the recognizable career narrative.

### Aggressive

Rebuilds emphasis around the target JD and may reorder, condense, or omit lower-relevance supported content. The truth boundary does not change: no invented skills, employers, dates, titles, tools, certifications, metrics, or outcomes.

## Personal OpenAI credential

A candidate may save an OpenAI API key from the Resume Studio UI only when the server-side encrypted vault is available.

Storage contract:

- plaintext key is never stored in Git;
- plaintext key is never stored in ordinary SQLite settings;
- the key is AES-GCM encrypted by `app.secure_vault` using `MUNSHI_VAULT_KEY`;
- the account label is derived from the current tenant/user identity;
- status APIs expose only non-secret configuration state;
- a server `OPENAI_API_KEY` remains a fallback when no usable personal key exists;
- a present but undecryptable personal key fails closed instead of silently using another credential.

## Cost controls

The user can choose:

- model;
- reasoning effort;
- max output tokens per request;
- max GPT calls per resume: 1 or 2.

Two calls permit one automatic content-budget repair pass. A one-call limit fails rather than silently making a second request.

These are application-side request constraints, not an OpenAI account-level billing cap.

## Current model choices

The UI exposes the current OpenAI GPT-5.6 family used by this project:

- `gpt-5.6-luna` — lower-cost;
- `gpt-5.6-terra` — balanced default;
- `gpt-5.6-sol` — highest-quality option.

## Files

- `app/native_resume_service_v2.py`
- `app/resume_studio_page_v2.py`
- `app/resume_studio_page.py` compatibility entry point
- `migrations/028_native_resume_studio_v2.py`
- `tests/test_native_resume_service_v2.py`
- `.github/workflows/native-resume-studio.yml`

## Deployment impact

None automatically. This branch must follow normal PR → CI → staging verification. No production deployment trigger is added.
