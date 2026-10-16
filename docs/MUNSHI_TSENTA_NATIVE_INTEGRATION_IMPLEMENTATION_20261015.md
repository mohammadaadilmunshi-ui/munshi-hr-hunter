# MUNSHI × Tsenta native integration foundation

**Evidence date:** October 15, 2026
**Branch:** `feat/linkedin-employer-url-resolution-20261015`
**Deployment status:** source and tests only; production and staging unchanged

## Outcome

This change turns the earlier inert Tsenta design into a tested connector
foundation while keeping external execution disabled by default.

Implemented:

- authenticated MCP adapter contracts for account/profile, application reads,
  job-description fetches, staged custom-resume validation, and the two exposed
  apply operations;
- in-memory Candidate Truth comparison that resolves protected local facts
  through the encrypted details reader instead of treating vault references as
  missing values;
- a tenant-scoped, exact-opening provider ownership lock shared by Native and
  Tsenta;
- preparation, filling, paused, failed, provider-submitted, and independently
  employer-confirmed states;
- bounded retries, idempotency, audit events, and status projection into the
  existing application flow;
- privacy-minimized Tsenta review/status observations and Native prediction
  deltas;
- sanitized multi-ATS regression fixtures;
- safer Prepare-to-Apply diagnostics that preserve bounded machine error codes
  while redacting URLs, email addresses, and credential-like text.

Not implemented or activated:

- no **authorized live** Netcup Tsenta OAuth connection (source-level PKCE flow now implemented);
- no real Tsenta application call;
- no real custom-resume upload;
- no production or staging migration;
- no n8n change;
- no Apply extension release;
- no employer submit action or receipt claim.

## Phase 1 diagnostic evidence

### Source and runtime

| Surface | Verified source/state | Finding |
| --- | --- | --- |
| Feature worktree | `251018ba71cdec94cc6d8b0d2511a203d4147b8f` before this implementation | Clean starting point; PR #103 remained draft |
| Production Hunter | `/opt/munshi/repo`, `b8cd45ea03903cd7811db46ddd3ecb8566bf19bb` | Clean; older than this branch |
| Staging Hunter | `/home/munshi/munshi-staging-v1/repo`, `b8b6234a02f335c24a9930c17539c05195ec2e63` | Clean; separate runtime |
| Production Apply | `/opt/munshi/apply-production/repo`, `c9a52141cf6fcfe5f46553d8d43719f0e7810d4a` | Clean; API on loopback 8505 |
| Staging Apply | `/home/munshi/munshi-apply-staging-v1/repo`, `c3684bf1cce28892507d9b8a1e5c6270f8f12e3c` | Clean; API on loopback 19000 |
| Production submission worker | none in the container inventory | Correctly not started during this task |
| n8n | 29 workflows, 1 active | Active workflow `L1u2xZkgFpi7KEuv`; no workflow mutation performed |

All inspected loopback health endpoints returned HTTP 200. The read-only
inspection also confirmed the five-layer Hunter Compose contract and the n8n
volume mounted read-only into Hunter. No database, container, workflow, account,
or deployment state was changed.

### Prepare and Apply boundary

The production ledger showed that package creation is not the principal failure:

- 62 Hunter prepare operations: 54 `FAILED_SAFELY`, 8 `CANCELLED`;
- 45 preflight packages: 44 `READY_TO_APPLY`, 1 `NEEDS_INPUT`;
- 41 accepted Hunter-to-Apply plan handoffs;
- 0 native application preparation rows, 0 lifecycle rows, 0 verified receipts;
- 41 Apply plans accepted, but 27 Apply prepare jobs were `FAILED_SAFELY`, 7
  `WAITING_INPUT`, and 1 `BLOCKED`;
- the browser/form stage concentrated the failures.

Observed failure classes included stale hosted plans, missing ATS account
credentials, browser event-loop conflicts, file-upload timeouts, bridge
transport/TLS failures, and correlated-email timeouts. The existing Hunter
catch-all reduced many of these to `ATS_FORM_PREPARATION_FAILED`. This change
retains safe machine codes and keeps customer/private response bodies out of the
ledger.

## Evidence-based gap matrix

| Capability | Existing reusable authority | Root finding | Implemented here | Remaining gate |
| --- | --- | --- | --- | --- |
| Job discovery | Hunter adapters and scoring | Not a connector gap | Reused unchanged | None for this scope |
| Official employer URL | signed LinkedIn V5 evidence and canonical non-LinkedIn URLs | Coverage is incomplete; exact matching is reliable where evidence exists | Router requires exact signed/canonical destination | Expand signed ATS-board coverage |
| Candidate Truth | confirmed profile snapshot, encrypted protected details | protected `value_reference` rows looked missing to the old generic comparator | Trusted in-memory protected-value comparison | User review of true conflicts; no automatic overwrite |
| Resume | Native resume and preflight digest bindings | package generation usually succeeds; browser attachment remains unproven E2E | Router binds exact resume SHA; custom Tsenta upload must pass `check_custom_resume` | Staging PDF upload and attachment proof |
| Answer Brain | catalog, vault, Universal Brain | Apply runtime questions can exceed pre-known requirements | Observation semantics map to the same catalog | Per-ATS fixture expansion and staging browser proof |
| Prepare Application | durable orchestrator, preflight, mandate and plan handoff | failure concentration is Apply browser/form execution | safer classified failures; no authority expansion | Staging E2E to `READY_FOR_REVIEW` with correct file and answers |
| Provider selection | none durable across Native and Tsenta | simultaneous engines could race | exact-opening ownership lock and API | UI selection control and authenticated worker wiring |
| Retries | task retries existed, provider-neutral retries did not | external provider attempts lacked one shared budget | atomic five-minute backoff and bounded route attempts | worker lease/scheduler staging proof |
| Submission state | several legacy state vocabularies | provider completion was easy to confuse with receipt | separate `PROVIDER_SUBMITTED` and `EMPLOYER_CONFIRMED` | employer confirmation collector staging proof |
| Dashboard/extension consistency | Hunter and Apply each expose state | no shared provider route existed | route projected in Hunter application-flow API | Apply extension consumption and version reconciliation |
| Learning | question catalog and ATS adapters | no privacy-minimized external observation contract | value-free observations, evidence grades, deltas, fixtures | use deltas to patch each Native adapter and prove regressions |

## Authenticated Tsenta capabilities

The live account exposed 74 MCP tools. The connector uses only the verified
public contracts below.

| Operation | Verified schema/capability | Connector behavior |
| --- | --- | --- |
| `get_profile_status` | profile readiness and missing application fields | read-only |
| `list_resume_profiles` | named profiles and default identity | read-only; choose explicit/default profile |
| `get_resume_profile` | full profile groups | compare in memory; never persist wholesale |
| `get_apply_preferences` | optimization/approval/submission preferences | summary flags only |
| `get_auto_apply_state` | blockers, config, schedule and profile availability | diagnosis only; no settings mutation |
| `fetch_job_description` | `url` -> status, ATS and job description | exact HTTPS employer URL only |
| `apply_to_job` | exactly one of URL/job ID/batch; optional profile and JD | implemented behind explicit call authorization plus off-by-default flag |
| `request_resume_upload` | signed direct-upload URL and upload ID | ephemeral return; signed URL must not be persisted |
| `check_custom_resume` | validates staged PDF and size | required before custom-resume dispatch |
| `apply_with_custom_resume` | exact job plus upload ID and complete profile | implemented behind the same independent execution gates |
| `list_applications` | six status buckets and counts | read-only, paged |
| `get_application_status` | state, action, error and optional heavy resume fields | connector requests lean default response only |
| `get_application_review` | paused form fields, values, types, choices and editability | values are inspected only to record presence, never retained |
| `list_tracked_applications` | user-visible recruitment pipeline | read-only duplicate/history aid |

Schema availability is not execution proof. The Mac Codex OAuth session is not
a backend credential and must never be copied to Netcup.

## Authenticated profile comparison

The authorized comparison was run in memory. Raw values were not logged or
committed.

- The default Tsenta profile was fully extracted and reported no required
  profile gaps.
- The old generic comparison incorrectly treated protected Candidate Truth facts
  as absent because their public snapshot contains vault references, not values.
- The corrected comparison reads those protected values from the trusted
  encrypted detail authority only for the duration of comparison.
- Work authorization and sponsorship boolean answers matched across the two
  systems in the observed account.
- The corrected bounded comparison produced 13 matches and 5 review conflicts;
  this count is diagnostic evidence, not permission to copy either profile.
- Other vocabulary/location/profile conflicts remain review items. A conflict
  is not permission to overwrite either system.
- The output contains field status labels and counts only.

## Application routing contract

```text
Hunter discovered job
  -> exact signed/canonical employer URL
  -> eligibility and prior-application checks
  -> current Candidate Truth + tailored resume + answers
  -> fresh READY_TO_APPLY preflight package
  -> one exact-opening provider ownership lock
       -> NATIVE or TSENTA, never both
  -> filling / paused / failed / bounded retry
  -> provider-submitted
  -> independently employer-confirmed
```

The opening identity uses employer URL, requisition when available, company and
title. It deliberately excludes Hunter's internal row ID so duplicate discovery
rows for the same exact opening cannot select competing providers.

The selection endpoint only creates/replays the ownership record. It does not
call Tsenta or Native Apply. Dispatch envelopes remain digest-bound,
value-free, `submit_authorized=false`, and off by default.

## Observation and learning contract

### Observed

Authorized paused-review records exposed field label, control kind, required,
editable, answer-presence, and option-count information. Sanitized examples
covered Dayforce, SAP SuccessFactors, and BrassRing, including legal checkboxes,
State/Province, gender/self-identification, work authorization, sponsorship, and
resume file fields.

Status records exposed provider state and safe failure codes, including observed
manual-completion and screener-rejection outcomes.

### Inferred

A Native field predicted but absent from an observed review is labeled
`INFERRED`, not “wrong.” A semantic-and-kind match is weaker than an exact field
fingerprint match and is recorded as such.

### Unavailable

The inspected tools did not expose browser DOM, selectors, page screenshots,
network requests, proprietary mapping logic, or an independent employer receipt.
Tsenta `COMPLETED` therefore maps only to `PROVIDER_SUBMITTED`.

The observation store excludes raw answers, option wire values, field IDs,
application IDs, employer URLs, resumes, OAuth material, cookies, and browser
internals. Provider application IDs are one-way hashed.

## Code map

- `app/tsenta_mcp_adapter_v1.py` — injected authenticated transport boundary and
  double-gated execution methods.
- `app/tsenta_profile_bridge_v1.py` — status-only Candidate Truth comparison,
  including protected local facts.
- `app/application_provider_router_v1.py` — exact-opening lock, state machine,
  retries, audit events and employer receipt promotion.
- `app/tsenta_observation_v1.py` — sanitized review/status contracts,
  persistence and Native prediction deltas.
- `app/product_application_flow_v2.py` — shared provider-route projection.
- `app/api.py` — authenticated flow read and provider-selection endpoints.
- `app/prepare_worker_adapters_v1c.py` — classified/redacted Apply failures.
- `migrations/058_tsenta_provider_learning_foundation_v1.py` — additive tables.
- `tests/fixtures/tsenta_mcp_application_reviews_sanitized_v1.json` — reusable
  value-free ATS review fixtures.

## Verification completed

- profile reads call all five required account operations;
- profile reports contain no raw profile values;
- protected local facts are compared from encrypted values, not vault URIs;
- work authorization and sponsorship comparisons are explicit;
- external job URLs must be public HTTPS and match canonical/signed evidence;
- provider lock prevents Native/Tsenta races across duplicate local job rows;
- preflight, Candidate Truth and resume digests are bound to the route;
- provider IDs are hashed and preserved across status updates;
- provider completion cannot become employer confirmation without separate
  evidence;
- retry backoff and attempt budget are durable;
- observations persist idempotently without answers or wire values;
- Apply diagnostics preserve safe error codes and redact private text;
- existing Original Job, Apply Job and Prepare actions were not removed or
  merged.

## Still blocked

- Backend-supported Tsenta OAuth and tenant-scoped token lifecycle are not
  established.
- No real `fetch_job_description`, upload, apply, approve, or submit call was
  made from this implementation.
- Exact tailored PDF identity has not been proven in a staged Tsenta application.
- Employer receipt collection is not wired for Tsenta.
- Native browser preparation has no current staging proof to
  `READY_FOR_REVIEW` across Workday, Greenhouse, Lever, Ashby, SmartRecruiters,
  and the observed Dayforce/SuccessFactors/BrassRing shapes.
- The installed Apply extension version remains unverified against the inspected
  source manifest.
- The locked requirements file is not fully installable from the currently
  configured package index, so a clean reproducible full-suite environment is a
  release blocker.

## Staging readiness requirements

Before this can be enabled in staging:

1. Review and apply migration 058 to the isolated staging database with backup
   and rollback proof.
2. Provision a supported staging Tsenta OAuth connection; never reuse the Mac
   Codex token.
3. Keep both external execution flags false for migration and read-only profile
   comparison.
4. Verify one exact synthetic/staging job URL, eligibility result, Candidate
   Truth digest, resume SHA, preflight package and provider lock.
5. Prove dashboard and extension read the same route/application identity.
6. Run simulated MCP failures: unavailable tool, expired upload, duplicate job,
   missing profile field, unsupported ATS, provider timeout and retry exhaustion.
7. Prove Native browser preparation reaches `READY_FOR_REVIEW` without final
   submission and with the correct resume attached.
8. Verify no raw profile, answer, signed URL, token, provider ID, or resume enters
   logs or analytics.
9. Reconcile source and installed extension versions.
10. Run the full reproducible CI suite on Linux and the staging deployment
    contract without touching production.

## Real-submission readiness requirements

Real submission remains prohibited until every staging gate passes and all of
the following are true:

1. A user-configured authorization policy explicitly allows the selected
   provider and exact opening.
2. The route has one provider owner, a fresh exact preflight package, verified
   employer URL, eligible job, current Candidate Truth, correct work
   authorization answers, correct resume SHA and no duplicate application.
3. Custom-resume mode has a validated, unexpired upload ID and a complete profile;
   default-profile mode has an explicitly selected/current Tsenta profile.
4. Retry behavior is idempotent under lost responses and concurrent workers.
5. Provider submission and employer confirmation are displayed separately.
6. An employer confirmation page, correlated employer email, or employer portal
   record supplies independent receipt evidence.
7. Production migrations, secrets, rollback, observability, worker activation
   and exact-SHA deployment are separately reviewed and approved.
8. PR #103 remains draft until these requirements are evidenced.



## Independent completion review: additional hardening

A separate isolated Git worktree reviewed this foundation before branch publication.
The review found that the original adapter treated nested Codex JSON/MCP tool
envelopes as business results and could mistake an \`isError\` or failed tool
result for success. The adapter now unwraps nested \`structured_content.result\`
and JSON text correctly, fails closed on tool errors without echoing their
possibly private error messages, and rechecks a staged custom-resume upload
immediately before its application creation call. Provider event codes must
now be bounded machine identifiers; free-text errors are reduced to the
constant \`PROVIDER_FAILURE_UNCLASSIFIED\` rather than being stored in the
audit ledger. Synthetic regression tests cover the failure forms.

These tests do **not** authenticate Netcup to Tsenta, create a real candidate
application, verify an employer receipt or prove a real ATS form completion.
Such effects remain disabled pending the documented staging and authorization
checks.


## Existing-account status reconciliation (read-only)

`app/tsenta_provider_status_sync_v1.py` can poll a bounded number of
`list_applications` pages using the injected authenticated transport and
match only a previously stored SHA-256 of a Tsenta application identity.
It does not guess identity from company names, titles or employer URLs, and
will not create, retry or approve provider applications. The provider's raw
application IDs and candidate details are processed only in memory. Matching
provider `COMPLETED` advances MUNSHI only to `PROVIDER_SUBMITTED`, never
to `EMPLOYER_CONFIRMED`. An independent receipt process remains a required
future integration. The reconciler is a tested library function, not a
configured Netcup scheduled worker.

## Original Dashboard UI/UX implementation

The Tsenta/Native controls are integrated into the **original MUNSHI Hunter
dashboard**, not an alternative standalone Tsenta dashboard.

User journey:

1. **Dashboard:** A compact Application engines panel shows that MUNSHI Native
   preparation exists, Tsenta backend connection is pending, and displays
   privacy-minimized form-observation/event counts from the user's tenant.
2. **Job cards and Job details:** Existing Original Job, employer Apply Job,
   and Prepare application actions remain intact. Already-routed jobs display
   their owning engine; job details display separate engine/progress and
   employer-receipt wording without exposing provider IDs.
3. **Applications workspace:** The existing Prepare/Review/Apply/Receipt
   sequence remains authoritative. An Application engine section renders
   the selected provider, provider-progress state, verification status,
   and safe diagnostics (attempt count, never raw answers).
4. **Selection preconditions:** No route is created when a job is ineligible,
   a signed LinkedIn employer destination is absent, a preflight package is
   missing or not READY_TO_APPLY, an application is already submitted, or
   an exact opening is already reserved.
5. **Tsenta backend disconnected:** The provider can be inspected in the UI,
   but the Save action is disabled. Local Codex OAuth is never presented as
   an authenticated Netcup backend connection.
6. **Eligible Native choice:** Only a deliberate, checkbox-confirmed Save
   action may create one durable provider ownership record. It never starts
   a browser, calls MCP, submits, or claims an employer receipt.

UI code:
- `app/application_provider_ui_v1.py`
- `app/product_pages.py`
- `app/unified_job_workspaces_v1.py`

UI regression coverage:
- `tests/test_application_provider_ui_v1.py`
- Real Streamlit `AppTest` smoke validates the radio choice and disabled
  Tsenta action before supported backend authorization, without real
  applications or database mutations.

**UI deployment gate:** Do not deploy this source to the original dashboard
until PR #103 and #104 pass CI, the new migration is proven in isolated staging
with rollback, and the original-dashboard staging URL has been smoke-tested.
The initial UI intentionally stops at read-only learning and provider
reservation; real execution is not yet available.

## Backend Tsenta authorization readiness

A read-only check of Tsenta's public OAuth authorization-server metadata
confirmed authorization-code, S256 PKCE, refresh-token and dynamic-client
registration capabilities. This is protocol discovery, **not** a Netcup
authorization or completed secure callback configuration.

The initial dashboard uses `app/tsenta_backend_readiness_v1.py` as a single
fail-closed server-authentication status source. The authenticated provider
selection API rejects Tsenta with HTTP 503 until there is a genuinely verified
backend OAuth connection. Neither a local Codex MCP session nor flipping a
feature flag can make this status pass. Production and staging remain on their
previous SHAs.

Completing backend authorization will require a securely routed HTTPS callback
on the initial dashboard, tenant-bound short-lived PKCE state, encrypted
per-tenant OAuth access and refresh token storage, refresh/revocation handling,
verification of a real authenticated Tsenta profile read, and explicit user
authorization. No token or account value is embedded in source or copied from
Codex. The current dashboard HTTPS front door returns HTTP 401 without browser
authentication; its OAuth callback/reverse-proxy routing requires review before
any live authorization attempt.


## October 15 implementation extension — OAuth source and initial-dashboard UX

Code added on PR #104, still **disabled for live connection**:

- \`app/tsenta_oauth_v1.py\`: explicit PKCE start, DCR or configured client ID,
  hashed expiring one-use state, AES-GCM tenant-bound encrypted verifier/access/
  refresh tokens, one-time callback exchange, expiration/refresh and local
  disconnect. No Codex tokens or secrets ever copied.
- \`app/tsenta_mcp_http_v1.py\`: bounded JSON-RPC Streamable HTTP read transport
  with legacy session initialization, JSON and SSE response handling, named
  read-only tool allowlist, and provider error redaction.
- \`app/tsenta_backend_readiness_v1.py\`: reports actual account authorization
  and provider profile-read verification, while \`execution_ready\` remains
  **false** until independently verified application dispatch exists.
- \`app/api.py\`: authenticated connection start/status/verify/disconnect
  endpoints and one-time OAuth callback endpoint.
- \`app/application_provider_ui_v1.py\`: original dashboard expandable
  connection controls and account-state captions, not a new dashboard.
- \`migrations/059_tsenta_backend_oauth_v1.py\`: additive tenant OAuth tables.
- \`.env.example\`: documented non-secret redirect/client/ingress guard
  settings, all disabled by default.

The authenticated provider OAuth metadata lists authorization, token, dynamic
registration and revocation endpoints under \`/oauth\`. Contract tests use
synthetic HTTP clients and exercise expired states, DCR request, replay
prevention, encrypted token storage, refresh, MCP profile verification, SSE
tool results, forbidden execution tools, API callbacks and migration replay.

**Netcup/staging operational blockers:**
1. Read-only October 15 inspection identified the staging Caddyfile at
   \`/home/munshi/munshi-staging-edge/Caddyfile\`; it contains proxy/auth rules,
   but no explicit Tsenta callback route. The public dashboard returned 401
   for an unauthenticated request. A deliberately scoped HTTPS callback route
   must be reviewed, applied and externally tested before the ingress-ready
   switch can be enabled.
2. An actual user approval on Tsenta for the Netcup OAuth connection remains
   necessary. The Mac's authenticated Codex MCP session is not Netcup OAuth.
3. OAuth profile verification is read-only and does not provide a tested
   dispatch worker, correct custom-resume upload, ATS complete-form evidence,
   employer submission or independent employer receipt.
4. Workday/native Prepare -> Fill -> Review and hosted Apply/extension state
   alignment must pass independent isolated staging end-to-end tests.
5. Staging database backups, migration 058/059 apply/replay/rollback, CI and
   exact production SHA/rollback must be reviewed before release.

**The original dashboard code is a reviewed development candidate, not a
completed production application-execution service.**
