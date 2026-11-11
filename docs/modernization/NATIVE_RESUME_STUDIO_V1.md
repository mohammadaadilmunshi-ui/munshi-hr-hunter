# Native Resume Studio V1

Status: `IN_PROGRESS`

Branch: `feat/native-resume-studio-v1`

Base: `feat/autonomous-career-os-foundation` at the post-Staging-Release-2 documentation head.

## Product goal

MUNSHI should let a candidate open a stored job, generate a truthful ATS-friendly tailored resume, review and revise it inside the website, download/use the exact artifact, and later hand that immutable artifact to MUNSHI Apply.

The candidate should not need to know that n8n exists.

## Current authority boundary

- n8n remains the authoritative resume-generation path until native parity is proven.
- `app/native_resume_shadow.py` remains a planning/comparison ledger only.
- Native Resume Studio V1 must not silently replace the Master Resume.
- Native Resume Studio V1 must not mark an application Submitted.
- No production deployment is authorized by this work.

## V1 resume contract

The native writer must return structured `native-resume-v1` content rather than free-form PDF bytes.

Every substantive candidate-facing claim must carry one or more candidate evidence IDs before it can pass into the renderer. Evidence IDs are audit metadata and must never appear in the candidate-facing resume.

The V1 ATS template is intentionally:

- single column;
- text first;
- white background;
- standard typography;
- semantic headings;
- no icons in the resume body;
- no charts/progress bars;
- no hidden keyword text;
- no multi-column layout;
- no em dashes;
- no unsupported claims merely to improve ATS coverage.

## First implementation slice

Implemented on this branch:

- `app/native_resume_studio.py`
  - structured evidence-backed resume schema;
  - contact, summary, education, skills, experience, project, and certification models;
  - mandatory evidence references for substantive content;
  - deterministic V1 content-budget diagnostics;
  - evidence-ID audit collection;
  - HTML escaping;
  - single-column ATS-safe HTML renderer;
  - no model/network/database/authority side effects.
- `tests/test_native_resume_studio.py`
  - evidence requirement;
  - em-dash rejection;
  - skill de-duplication;
  - renderer evidence secrecy;
  - HTML escaping;
  - single-column guard assertions;
  - content-budget diagnostics.

## Remaining slices

1. **Versioned persistence**
   - additive migration;
   - tenant/user ownership;
   - job + Master Resume lineage;
   - immutable resume version IDs;
   - content SHA-256;
   - exact evidence ledger;
   - no silent overwrite.

2. **Evidence bundle builder**
   - Master Resume;
   - confirmed Digital Twin evidence;
   - complete stored JD;
   - current candidate facts;
   - protected/sensitive fact exclusion;
   - explicit missing-evidence state.

3. **GPT writer boundary**
   - model call only after evidence bundle construction;
   - structured JSON output validated into `ResumeDocument`;
   - fail closed on unsupported/unparseable claims;
   - configurable model, no hardcoded secrets;
   - no direct PDF generation by the model.

4. **Truth / claim audit**
   - every generated summary sentence/bullet/skill/education/certification claim must remain evidence-backed;
   - unsupported metrics/tools/dates must fail validation or be removed;
   - protected self-identification must remain excluded.

5. **JD / ATS coverage**
   - deterministic exact-term and semantic coverage evidence;
   - explain missing concepts;
   - never add an unsupported skill to improve a score;
   - do not fabricate a score when the scoring contract is unavailable.

6. **Physical renderers**
   - ATS HTML preview;
   - PDF artifact;
   - DOCX artifact;
   - physical page-count verification;
   - readable one-page guard rather than shrinking typography indefinitely.

7. **Website Resume Studio**
   - Generate ATS Resume from stored job;
   - preview in website;
   - ATS/truth/budget status;
   - AI revision prompt;
   - section locking;
   - version history;
   - compare/restore;
   - Download PDF/DOCX;
   - Use for Application.

8. **n8n parity gate**
   - compare native artifact with known n8n baseline;
   - preserve n8n authority during shadow/parity period;
   - require explicit acceptance criteria before native authority can be enabled.

9. **MUNSHI Apply handoff**
   - send exact native artifact ID + SHA only after it is accepted for application use;
   - Apply uploads the exact artifact;
   - preparing a resume still does not mean Submitted.

## Deployment rule

Normal path remains:

`feature branch -> draft PR -> CI -> review -> staging -> explicit production promotion`

Do not auto-deploy this branch to production. Do not start Phase 12 browser/AutoPilot authority as part of Resume Studio V1.
