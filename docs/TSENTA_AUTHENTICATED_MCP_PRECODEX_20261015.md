# Tsenta MCP authenticated pre-Codex capability audit

**Date:** October 15, 2026  
**Repo:** `mohammadaadilmunshi-ui/munshi-hr-hunter`  
**Worktree at audit:** `/Users/aadil/PROJECTS/MUNSHI_LINKEDIN_RESOLVER`  
**Branch before this documentation commit:** `feat/linkedin-employer-url-resolution-20261015`, `c5880c8a377e44d351a935979b24345472ae8dfb`

## 1. Actual connection and scope

### Same-day implementation refresh

The subsequent read-only authenticated audit exercised the required profile and
application-read operations and inspected their current schemas. It made no
apply, upload, approval, submit, preference, automation, or account-setting
mutation.

- Profile status, resume profile list/full profile, apply preferences and Auto
  Apply state all returned successfully.
- Four paused application reviews returned field-level form records.
- Application status failures and successful provider completions were read.
- `request_resume_upload` and `check_custom_resume` schemas were inspected but
  not called.
- External application operations were not called.

- Mac `codex-cli 0.160.0` registered **tsenta** using `codex mcp add tsenta --url https://api.autojobs.me/api/v1/mcp`.
- OAuth flow reported **Successfully logged in**. `codex mcp list` displayed Tsenta as enabled, with OAuth authentication, using Streamable HTTP.
- Credential/token material stayed in the local MCP client. No bearer token, cookie, Workday password, SSN, passport details, raw profile, or resume was committed or reproduced in this audit.
- This is **local Codex-client authorization**, NOT Netcup backend OAuth or proof that production MUNSHI has a connected Tsenta session.
- No user-submission, application approval, profile edit, upload, deployment, queue write or production database mutation took place.

## 2. Actual tool discovery

An authenticated Codex MCP metadata inventory exposed **74** tools in the `mcp__tsenta__` namespace. Only key connector-facing examples are listed below; do not hardcode names/schemas without re-querying the live server.

| Objective | Discovered tool(s) | Integration meaning |
|---|---|---|
| Existing saved profile | `get_profile_status`, `list_resume_profiles`, `get_resume_profile` | Read already-saved Tsenta data if permitted, compare with MUNSHI Candidate Truth |
| Apply/profile settings | `get_apply_preferences`, `get_auto_apply_state`, `get_application_balance` | Learn preferences/readiness, never overwrite without a separately approved action |
| MUNSHI-discovered external ATS job | `apply_to_job` (accepts `url`, `jobId` or `jobIds[]`) | Potential direct MUNSHI → Tsenta handoff; **schema-exposed, not successful submission-tested** |
| Alternative custom profile and tailored PDF | `apply_with_custom_resume` (`profile`, `uploadId`, plus `url` or `jobId`) | Optional MUNSHI-supplied profile mode rather than Tsenta-account-default mode |
| Read ATS job | `fetch_job_description` (`url`) | Read-only external URL intake; one historical Figma pilot call **failed**, exact cause not classified |
| Read applications | `list_applications`, `get_application_status` (`applicationId`) | Provider-reported states and metadata |
| Inspect paused pre-submission form | `get_application_review` (`applicationId`) | Successfully exercised on four paused account records; field values may be observed in memory but must not be retained |
| Track prior entries | `list_tracked_applications` | May help duplicate checks and account history |
| Actual execution and review | `approve_application`, `submit_application_review` | **Never call during discovery; separate explicit submission authority required** |

Other categories include profile setters, resume layouts/sections, feed and automation mutations, inbox/message operations, networking, subscription and support. These are **not** part of an initial read-only connector build.

An additional traced `codex exec --json` session showed actual MCP call lifecycle events:
- `server=tsenta`, `tool=get-profile-status`, `item.completed`, `status=completed`
- `server=tsenta`, `tool=list-applications`, `item.completed`, `status=completed`
- The list-applications tool event used a `result` object with `content` and `structured_content`.

Note: Some temporary Codex metadata-only sessions did not resolve the tool catalog unless the exact `mcp__tsenta__...` tool name was explicitly requested. Connector startup must verify tool availability with the live server, report failures, and retry safely; do not silently infer capabilities from prior sessions.

## 3. Profile/data observations from authorized read-only session

The authenticated profile-summary session returned:
- **One** saved resume profile; the full profile getter was reported successful.
- Available structured groups include personalInformation, education, workExperience, workAuthorization, workPreferences, skillCategories, certifications, projects, backgroundCheck, additionalInfo, resume layout/styling, and optional sections.
- Work authorization fields include `visaType`, `optStatus`, `needsSponsorship`, `isAuthorizedToWork`, `citizenshipCountries`, `workAuthorizations`.
- Contact fields and many profile categories were populated, but **raw personal values are intentionally omitted from this repo**. Never infer those values from this report.
- A read-only application summary reported **74 total**: 60 `submitted`, 4 needing action, 4 failed, 6 skipped, 0 in flight. These are **Tsenta-side status labels, not independently corroborated employer receipts**.
- Of a bounded 25-record application sample, Workday was common (11), indicating meaningful potential for comparing Workday behavior with MUNSHI Native.
- `get_application_status` returned metadata fields (execution states, ATS, resume readiness, timestamps, submission identity) but no independently confirmed field-by-field submitted answers or employer receipt in the default observed response.
- The four bounded paused reviews exposed Dayforce legal checkboxes,
  SuccessFactors missing State/Province and gender fields, and a BrassRing form
  containing work authorization, sponsorship, resume, contact, employment,
  salary, EEO and legal-attestation shapes. Raw answers, field IDs, employer
  identity and application IDs were not retained.
- Four bounded failed status records showed two provider-classified manual
  completion outcomes and two screener-rejection outcomes. These are Tsenta
  provider records, not independent employer receipts.

**Important evidence grading:** The aggregate account counts and profile field categories above were presented by the authenticated Codex read-only summarization. Separate JSON event traces independently confirm completed profile-status and application-list MCP calls. The raw account response was not stored as a permanent reproducibility artifact, deliberately minimizing personal-data retention.

## 4. Findings for the learning objective

**Can Tsenta receive jobs found by MUNSHI?** The `apply_to_job` tool explicitly accepts an external `url`, and `apply_with_custom_resume` accepts `url` plus a prepared PDF upload and profile. This makes the connector architecturally viable. Actual behavior for an active eligible MUNSHI posting still needs a controlled pilot. One historical Figma `fetch-job-description` GET failed; no inference about all jobs is justified.

**Can it reuse the saved Tsenta account profile?** The existing profile is readable through authorized `list_resume_profiles/get_resume_profile`; the default-profile application path is a strong candidate but must be verified through safe real usage. Never copy a browser session or Codex OAuth credentials into backend deployment.

**Can MUNSHI learn how Tsenta filled forms?** Yes, narrowly: a paused
`get_application_review` response exposes every returned field's label, kind,
required/editable flags, value presence and choice list. MUNSHI may learn those
observable form shapes while discarding answers and option wire values. It still
does not establish access to browser steps, DOM/selectors, every submitted
answer, proprietary mapping logic, or an employer-side receipt.

**What learning is authorized?** Record only visible observations surfaced by user-authorized account/MCP tools: ATS, job and requisition identity, form-field key/type, source Candidate Truth category, validation outcome, upload result, page transition, provider status, error classification and independently verified employer receipt where available. Do not access proprietary/private Tsenta internals, scrape account cookies, or infer hidden algorithms.

## 5. Required connector architecture

```
MUNSHI Hunter (keep current job finder)
  -> verified Original LinkedIn + employer Apply URL
  -> Candidate Truth + prepared tailored resume + answer brain
  -> single-owner application router + exact-job idempotency lock
        -> Tsenta MCP adapter (existing saved account profile default)
        -> optional MUNSHI Native worker, never simultaneous for same job
  -> provider status / evidence / independently confirmed employer receipt
  -> sanitized observation mapper / comparison with Native ATS engine
  -> regression fixtures and targeted Native autofill fixes
```

- `app/tsenta_profile_bridge_v1.py` already provides an **offline** authorized-status-only profile comparison and **inert** digest-bound handoff. It has no real MCP connection.
- Keep MUNSHI Candidate Truth authoritative. Compare fields in memory and review conflicts. Special attention to OPT, F-1/work authorization, sponsorship, employment-current-status and eligibility questions.
- Do not treat a hash-only handoff as a complete executable application. A real adapter must transform the authorized application package into an actual supported MCP call with correct profile/resume selection.
- Tsenta Native and MUNSHI Native may **not** submit the same employer requisition simultaneously.
- `provider_submitted`, `submit_clicked` and `employer_receipt_confirmed` are different states.
- Use isolated, tenant-scoped secrets and OAuth flow in production; the Mac Codex OAuth token is not the Netcup backend token.
- Avoid raw resumes, PII, tokens or user application answers in GitHub, diagnostic logs, link-resolution tables or analytics.

## 6. Implementation status

The tested source implementation now lives in:

- `app/tsenta_mcp_adapter_v1.py`;
- `app/tsenta_profile_bridge_v1.py`;
- `app/application_provider_router_v1.py`;
- `app/tsenta_observation_v1.py`;
- `migrations/058_tsenta_provider_learning_foundation_v1.py`.

See `docs/MUNSHI_TSENTA_NATIVE_INTEGRATION_IMPLEMENTATION_20261015.md` for the
gap matrix, tests, state model, and staging/real-submission gates. The code is
not deployed and execution remains off by default.

## 7. Remaining build and staging work

1. Inspect this doc and `AGENTS.md` before changes; refresh actual authenticated schemas and account-scope permissions.
2. Implement a read-only profile/account adapter first: list/get resume profiles, get profile status, compare Candidate Truth in memory, conflict-only diagnostics.
3. Repair/verify MUNSHI Prepare Application independently (profile/job/resume digest and durable queue operation). This was not repaired during Tsenta OAuth discovery.
4. Implement an authenticated provider-neutral application router with `apply_to_job(url)` and optional custom-resume mode, behind an **off-by-default execution flag**, correct ATS URL, eligibility gate and one-engine idempotency.
5. Add permitted observation capture from actual MCP review/status/receipts. **If full field detail is not exposed, document the limitation and ask for official export support rather than guessing hidden APIs.**
6. Stage one eligible job, exact application package, validated responses and files, and independently verified receipt. Do not claim success from a tool call alone.
7. Align extension with the same state store after backend behavior is proven.

## 8. Shipping restrictions

- **Do not merge/deploy PR #103** until V5 signed evidence, employer board coverage, staging database migration, two-link UI and Prepare Application E2E are demonstrated.
- No automatic submit during capability discovery.
- No sharing OAuth tokens, secrets or private profile content in source control.
- Preserve historical jobs and both existing button placements.
- Use real employer requisition corroboration; do not guess Apply links.
- Keep Netcup production unchanged until explicit, reviewed, exact-SHA promotion.
