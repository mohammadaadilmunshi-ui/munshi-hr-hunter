# Staging Release 1 deployment record

Status: `STAGING_RELEASE_1=DEPLOYED_AND_VERIFIED`

Deployment timestamp: 2026-09-04T16:06:49Z

## Release identity

- Reviewed and deployed SHA: `b0a45dfa529797800a4860f84ac6d545f6cc9c1e`
- Source branch: `feat/autonomous-career-os-foundation`
- GitHub workflow: [Netcup Staging Deploy run 33893042880](https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter/actions/runs/33893042880)
- Previous staging SHA: `2687480625e2daa4210cf49f3dab28899e8f0043`
- Staging URL: <https://staging-dashboard.munshi.systems/>
- Deployment source checkout: attached to the source branch at the reviewed SHA (not detached).

The later documentation-only branch commit `9fb4fc467cd288617d884bb8392a9b3e7fc999cd` was not deployed. The workflow was dispatched with the exact reviewed `commit_sha` above.

## Deployment and rollback evidence

- The manual workflow request validation, Ubuntu/Linux/Docker CI, portability checks, deployment-shell checks, Hunter image build, and JobSpy image/runtime contract passed.
- The canonical restricted-SSH staging wrapper created and quick-checked a WAL-safe predeploy database backup under `/home/munshi/munshi-staging-v1/backups`.
- Automatic rollback remains available through the wrapper's preserved previous SHA and rollback Hunter image. Previous SHA: `2687480625e2daa4210cf49f3dab28899e8f0043`.
- The wrapper recreated Hunter only. n8n, Ollama, and the staging HTTPS edge were each verified unchanged; production container identity was unchanged.

## Runtime verification

- Deployed SHA and attached branch: verified by SSH.
- Staging runtime contract: PASS — clean source, healthy containers, side-effect flags/process exclusions, staging-only volumes, loopback raw ports, SQLite `quick_check`, dashboard configuration, and HTTPS authentication boundary.
- Production runtime contract: PASS — five Compose layers, read-only n8n state contract, health checks, and database integrity. Production impact: **NONE**.
- Public unauthenticated requests to both protected dashboards return HTTP 401. Staging routing remains isolated from production.
- Migrations 015–023: deployed schema tables verified in the staging database.

## Synthetic tester fixtures

- Canonical seed tool dry run: PASS; proposed 19 fixtures with zero existing fixture rows.
- Seed result: 19 fixtures written to the isolated staging database only.
- The reviewed Hunter image does not package `tools/seed_staging_fixtures.py`; the seed was therefore executed inside the isolated Hunter interpreter from source bytes streamed from the attached staging checkout. The streamed source SHA was verified byte-identical to the reviewed release's canonical tool before both dry-run and apply. No container image or source state was modified.
- Fixture verification: all 19 rows are ledger-owned, labeled staging/test, use fictional employers and `.invalid` application URLs, have all external-action flags disabled, have no n8n results, and are excluded from `canonical_jobs_v1` metrics.
- Database integrity after seed: PASS.

## Product and safety verification

- Product UI/producer-boundary regression suite: 61 passed.
- Career OS Phase 0–8 plus staging fixture/migration regression gate: 69 passed.
- Native preparation contract verifies `READY_TO_APPLY` as a readiness-only status; tracker submission is reserved for recorded submitted evidence. **Prepare != Submitted: VERIFIED.**
- Sensitive self-ID question families are rejected from preparation/answer paths and remain separated from ranking/resume behavior by the focused regression suite.
- n8n authority: **PRESERVED**. No Telegram, Gmail, n8n submission, ATS/Apply, outreach, or real application action was triggered.

## Known limitation

Authenticated visual browser QA could not be completed in this session: the public boundary correctly requires authentication and the available browser-control plugin lacked its required runtime client. No credentials were inspected or bypassed. Automated UI regression, Streamlit health, API health, staging database checks, and runtime-contract verification all passed.

Do not begin Phase 9 from this release record.
