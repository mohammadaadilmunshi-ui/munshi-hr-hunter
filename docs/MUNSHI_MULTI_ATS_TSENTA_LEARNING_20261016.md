# MUNSHI Multi-ATS Tsenta Learning — October 16, 2026

Target: existing original dashboard at dashboard.munshi.systems, not a replacement staging site.

## Platform-neutral execution
MUNSHI Native and Tsenta are alternative engines for one durable, duplicate-safe application. Tsenta is an optional provider and authorized source of read-only learning evidence, not a replacement for Native. Applicant truth, resumes, job routing, history and independent employer receipts remain owned by MUNSHI.

ATS names may annotate observed form evidence but never define an exhaustive whitelist. The generic semantic scanner uses labels, ARIA, fieldsets, required controls, dependent selection, form navigation, upload and submit boundaries. Representative synthetic tests cover Workday, Greenhouse, Lever, Ashby, iCIMS, SmartRecruiters, Taleo, Oracle, Dayforce and an unknown ATS. SAP SuccessFactors, BrassRing and UKG need additional live independent E2E acceptance. Synthetic tests do not prove any live employer submission.

## Ten-to-fifteen learning cycle
1. Account owner explicitly connects Tsenta to the original dashboard through authenticated backend OAuth. Mac Codex access is not reused as a server credential.
2. An authenticated, read-only history sync requests at most 15 existing Tsenta applications per page. Only list_applications, get_application_status, get_application_review are used. It never calls apply, approve, retry or submit.
3. Store SHA-256 application references, hashed form-field fingerprints, semantic question category, field metadata, status and evidence grade. No applicant answers, resume content, employer links, raw field labels, provider choice wire values or Tsenta tokens are persisted in the learning table.
4. A processed application is one unique provider application whose latest status is OBSERVED and terminal. Repeated polls and dozens of fields never inflate the total.
5. Ten processed applications open a shadow evaluation cycle; 15 complete that cohort. The next distinct fifteen applications start the next cohort. Any new ATS family with a safe bounded identifier participates.
6. A shadow hypothesis is proposed only after observing the same required-but-missing field fingerprint in at least three distinct applications for the same ATS. No candidate answer is inferred.
7. Held-out Native replay requires at least five separate application digests not found in the training cohort, at least a 10-percentage-point recognition gain and no ATS-specific regressions or new false positive controls.
8. Even a passing replay permits only a non-sensitive field-priority pilot after release review. It does not automatically change legal, immigration or applicant answers, nor give submission authority.

## Implemented
- app/tsenta_learning_sync_v1.py: privacy-minimized bounded MCP import.
- app/tsenta_learning_cohort_v1.py: distinct terminal process cohorts, ATS coverage, shadow improvement proposals.
- app/tsenta_learning_replay_v1.py: replay evaluation and per-ATS regression detection.
- app/tsenta_observation_v1.py: dynamic ATS slugs and no durable raw question/error labels.
- app/api.py: protected import and cohort status endpoints.
- app/application_provider_ui_v1.py: learning progress and import on the initial dashboard.
- Apply v0.2.12 ATS browser matrix: known and synthetic unfamiliar form styles.

## Still missing real-world proof
No new Netcup Tsenta OAuth consent or live history import has happened. The 10/15 learning count has not been claimed for real data. No actual before/after Native replay, newly promoted live rule, full representative multi-ATS employer application, independently confirmed employer receipt, original dashboard Caddy route activation, or production deployment has occurred in this change. Never confuse tests with verified live completion.


## Release-safe learning semantics
The 10-15 threshold counts distinct, latest-observed terminal Tsenta
**processing outcomes**, including unsuccessful outcomes, never the number of
fields. It is evidence for learning, not a guarantee of an employer receipt.
A latest nonterminal update removes a reopened application from the current
terminal cohort. The learning table updates repeated machine-status evidence
without inserting duplicate application examples. Retained question labels,
answer values, option wire values and provider error-title text are excluded
from persistent storage; field fingerprints remain to support Native mapping.

Existing Tsenta history sync is owner initiated from the original dashboard
and requests at most 15 application IDs per page. It does not manufacture
browser traces, create applications, transfer documents, or execute the Tsenta
apply tool. Some historical completed applications may have no review fields
available, and this is reported honestly rather than inferred.

The held-out Native replay evaluator is a **shadow test**, not a self-modifying
production agent. Pilot candidates require at least five independent observed
applications, a 10-percentage-point improvement and no per-platform regression.
No answer generation, candidate truth, immigration responses, legal attestation
or submission behavior changes automatically. Cohort review and explicit code
rollout are separate from observed data collection.

OAuth connection state now always sets execution_ready=false until a separately
tested provider dispatch worker exists, even if a Tsenta profile read succeeds.
