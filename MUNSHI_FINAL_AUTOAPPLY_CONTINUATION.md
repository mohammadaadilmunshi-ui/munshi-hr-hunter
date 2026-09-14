# AutoApply preparation continuation

## Scope and anchors

Source/test only. No deployment, real applications, production database changes, or final-submit activation.
Hunter base: release/initial-dashboard-hunter-rc-v2-20260913 at eb61d05490a738327db20bd0f73bf7f56c3d12c9.
Hunter working branch: feat/autoapply-preparation-20260914 (resolve HEAD through GitHub).
Apply PR #24: feat/final-autoapply-loop-20260914 at df914cd0033f1af6fe9966a2ec30fae86d9bd5e2. Teach safety work preserved.

## Completed source change

ANSWER_RESOLUTION now invokes existing application_answer_autofill_v1.prepare_from_candidate_truth before taking its Answer Brain inventory.
This reuses confirmed NORMAL facts and existing structural projections; protected answers remain on their secure paths.
The worker checks the resolver's profile digest and rechecks Application Truth after refresh. Stale or failed refresh cannot complete the task.
Existing retry limits and per-operation task isolation remain unchanged.
Targeted tests cover refresh ordering, replay, missing protected facts, digest mismatch, concurrent edits, and failure isolation.
Executable results pending the dedicated targeted CI workflow. No local repository test result claimed.

## Not complete

The requested four-part product tranche is NOT complete.
Automatic admission, background runner integration across Native and n8n, ISSUE convergence, and NEEDS REVIEW convergence remain.
Do not relabel unresolved fields as resolved or claim the whole queue works.
No Apply source changes in this tranche.

## Exact next task

Check AutoApply preparation targeted workflow on this Hunter branch.
Then connect automatic admission to existing eligibility gates without creating duplicate n8n/native work.
Relevant inspected files: app/unified_hourly_coordinator.py, app/n8n_dispatch.py, app/product_application_flow_v2.py, app/prepare_worker_adapters_v1a.py, app/prepare_worker_adapters_v1b.py.
Coordinator currently calls queue_candidates then dispatch_pending for n8n.
product_application_flow_v2.start_prepare_for_job creates the durable graph on manual Prepare.
V1A explicitly leaves N8N resume tasks unclaimed; identify the existing n8n artifact completion path before choosing queue wiring.
V1B previously only read the existing Answer Brain inventory; this patch closes that specific gap.
After admission/worker integration, route genuinely unresolved applications to ISSUE while preserving evidence and continuing other jobs.
Do not start fallback wiring or final-submit work until this product tranche is proven.
