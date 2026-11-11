# Phase 3 — Candidate artifacts

`candidate_artifacts` is an additive, tenant-scoped index of existing n8n result URL fields. It does not upload, parse, fetch, fabricate, or delete resumes/documents. The normalizer accepts only non-empty HTTP(S) references already stored in `n8n_results`; those rows remain the evidence source and historical URLs stay unchanged.

Historic n8n result rows have no tenant identifier, so compatibility indexing is intentionally limited to the established `default/local-owner` singleton. A future authenticated ingestion bridge must attach tenant ownership at write time before another tenant can see an artifact.

`candidate_artifact_designations` retains master-resume designation history. A master is created only by an explicit user action selecting an indexed resume (`resume_pdf`, `resume_doc`, or `resume_docx`). Scoring, recency, page renders, and migration execution never promote an artifact. Clearing a master deactivates its designation; it does not delete the artifact record or the underlying n8n result.

For continuity, the former singleton `candidate_master_resume_v1` setting is adopted once only when its exact URL still resolves to an indexed n8n resume. That setting itself was produced by an explicit designation action. A stale, malformed, or unrecorded legacy URL is ignored; clearing the designation also clears that retired setting so it cannot be adopted again.

Resume ingestion boundary: this phase deliberately does not ingest uploaded files, extract resume text, invoke parsers, or connect to external storage. Such ingestion requires a separately reviewed trusted upload/provenance boundary.

Rollback: stop using the new product-state designation functions and retain both added tables. The migration is additive and does not alter `n8n_results` or existing stored references.
