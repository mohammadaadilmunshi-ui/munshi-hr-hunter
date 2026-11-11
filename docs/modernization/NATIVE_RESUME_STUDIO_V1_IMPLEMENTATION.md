# Native Resume Studio V1 — implementation and staging contract

Status: **IMPLEMENTATION IN PROGRESS — feature branch only**

Branch: `feat/native-resume-studio-v1`

Base: `feat/autonomous-career-os-foundation` at the post-Staging-Release-2 documentation head.

Production impact: **NONE**. Production deployment is not authorized by this work.

## Product goal

MUNSHI should let a candidate remain inside the website to create, inspect, revise, and download a job-specific ATS-friendly resume. The native writer is evidence-first: GPT may rewrite, prioritize, shorten, and reorder candidate-supported material, but it may not invent employers, titles, dates, education, skills, tools, metrics, certifications, locations, or outcomes.

This pre-Phase-12 milestone deliberately completes resume creation before cloud-browser AutoPilot work.

## User flow implemented

`Resume Studio` is a first-class product route.

1. Candidate imports or pastes a truthful source resume/career-history record.
2. Candidate reviews and explicitly saves that source.
3. MUNSHI builds an owner-bound evidence bundle from the confirmed source plus non-sensitive candidate profile/Digital Twin evidence.
4. Candidate chooses a stored job with a real job description.
5. Candidate explicitly clicks **Generate ATS resume**.
6. The configured OpenAI Responses API model returns the structured `native-resume-v1` document contract.
7. MUNSHI validates evidence references, numeric claims, identity/experience grounding, ATS content budgets, and JD term coverage.
8. Only a validated resume becomes an immutable native version.
9. Candidate previews the ATS-safe single-column resume in MUNSHI.
10. Candidate can download HTML/DOCX and render a physical Letter-size PDF through Chromium.
11. Candidate can issue a natural-language revision such as “make the summary shorter” or “emphasize analytics.” The revision becomes a new immutable version.
12. Candidate may lock contact, education, certifications, or experience during a revision.
13. Parent/child versions can be compared.

## Source ingestion

V1 supports candidate-confirmed:

- pasted text;
- `.txt`;
- `.md`;
- `.docx` (parsed locally with WordprocessingML).

PDF *source import* is intentionally not enabled in this V1 slice. This avoids adding an unreviewed PDF parser dependency merely for convenience. Generated PDF output is supported separately through the existing Chromium runtime.

The active source is tenant/user owned and stored by SHA-256. Re-saving identical content reactivates the existing source rather than creating duplicate evidence.

## Evidence rules

The writer receives only the current candidate's evidence bundle.

Evidence sources:

- candidate-confirmed native resume source segments;
- user-confirmed Candidate Digital Twin facts with evidence;
- legacy candidate-provided Profile facts as a compatibility bridge.

Sensitive/voluntary self-identification tokens are excluded before model construction. This includes race, religion, disability, ethnicity, gender, marital status, sex, veteran status, age/birth and citizenship/self-identification material.

The evidence bundle has a deterministic digest and stable evidence IDs. Visible resume output never renders evidence IDs.

## Truth guards

Before a model-written resume can be persisted:

- every referenced evidence ID must exist in the current bundle;
- summary and every evidence-backed bullet are checked for numeric claims;
- numeric values absent from the cited evidence fail closed;
- candidate name/contact, education, organization/title/date, project, and certification fields are checked against candidate evidence;
- sensitive self-ID is not available to the writer;
- em dashes are rejected by the document contract;
- unsupported fields fail instead of being silently repaired with invented content.

The system therefore blocks examples such as adding an unsupported `92%` classification-accuracy claim solely to improve a score.

## ATS-friendly document contract

Template: `ats-single-column-v1`

The renderer is deliberately conservative:

- one text column;
- Letter page target;
- Arial/Helvetica-compatible typography;
- centered name/contact line;
- conventional section headings;
- thin section rules;
- no charts;
- no graphics inside the resume body;
- no sidebars;
- no hidden keyword stuffing;
- no evidence IDs;
- no score text inside the resume;
- controlled word budgets for summary, bullets, and overall content.

The visual target is the clean ATS-safe resume reference supplied by the user, while preserving machine readability.

## ATS/JD diagnostics

The website exposes an explainable **MUNSHI ATS readiness estimate**. It is explicitly *not* represented as a score returned by an employer ATS.

Diagnostics include:

- deterministic readiness estimate;
- document word count;
- content-budget issues;
- evidence/truth-audit status;
- numeric-claim guard status;
- JD term coverage;
- matched JD terms;
- missing JD terms;
- source/evidence digests;
- physical PDF page count when the user asks MUNSHI to render the PDF.

A missing JD term never authorizes MUNSHI to fabricate the skill or experience.

## GPT / OpenAI boundary

Environment names only:

- `OPENAI_API_KEY`
- `MUNSHI_RESUME_MODEL` (default `gpt-5.6-terra`)

No API key is committed, returned in status, logged by the product, or stored in resume records.

No resume source is sent to OpenAI merely by viewing the page or saving evidence. A model call occurs only after the candidate explicitly selects **Generate ATS resume** or **Apply AI revision**.

If `OPENAI_API_KEY` is absent, the product reports the writer as not configured and refuses to fabricate a fallback resume.

## Version persistence

Migration `027_native_resume_studio.py` adds:

- `native_resume_sources`;
- `native_resume_versions`.

Resume versions preserve:

- owner;
- target job;
- source ID;
- parent version;
- monotonic job-specific version number;
- candidate instruction;
- section locks;
- model name and non-secret response ID;
- evidence digest;
- structured resume JSON;
- diagnostics JSON;
- rendered HTML hash;
- validation status;
- creation time.

Existing versions are never rewritten when the candidate revises a resume.

## Output formats

- HTML: ATS-safe renderer and website preview.
- DOCX: generated locally as a simple single-column WordprocessingML document.
- PDF: printed from the same ATS-safe HTML using the existing Chromium runtime. MUNSHI reports the physical page count after rendering.

## Authority boundary

Permanent during this milestone:

- `native_resume_authority_enabled() == False`;
- n8n remains authoritative for the existing guarded preparation path;
- native Resume Studio does not submit applications;
- native Resume Studio does not invoke MUNSHI Apply;
- native Resume Studio does not mark jobs Submitted;
- native Resume Studio does not automatically replace the Master Resume;
- **Use for application** stays disabled pending native-vs-n8n parity and an explicit authority review;
- Phase 12 cloud-browser AutoPilot is not part of this change.

## CI

A dedicated `Native Resume Studio` GitHub Actions workflow validates:

- compile/import safety;
- structured document tests;
- source/evidence isolation;
- sensitive evidence exclusion;
- numeric-claim fail-closed behavior;
- DOCX generation/import;
- immutable version history;
- missing API-key fail-closed behavior;
- no native resume authority;
- candidate artifact/tenant/product regressions;
- repository state guard.

Existing repository safety, Product UI, Linux, Docker, and deployment-transport workflows remain intact.

## Staging deployment gate

Before staging deployment:

1. All PR checks must be green.
2. Native Resume Studio focused tests must pass.
3. Migration 027 must be additive and unique.
4. No secret/runtime state may be tracked.
5. Exact staging candidate SHA must be recorded.
6. Deployment must use the existing `Netcup Staging Deploy` workflow and exact SHA.
7. Only staging Hunter may be recreated.
8. Staging n8n/Ollama/edge identities must remain unchanged.
9. Production identities must remain unchanged.
10. Staging database integrity and HTTPS boundary must pass after deployment.
11. Resume Studio route, source storage, evidence status, job selection, and render path must be verified in staging.
12. If no staging OpenAI key is configured, record `NATIVE_RESUME_GPT=BLOCKED_EXTERNAL` rather than injecting or exposing a secret through source control.

Production remains forbidden until a separate explicit production promotion decision.
