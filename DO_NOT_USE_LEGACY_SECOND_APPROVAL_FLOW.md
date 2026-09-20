# HISTORICAL ONLY: legacy second-approval / browser-babysitting flow

Status: RETIRED. DO NOT REUSE, REVIVE, CHERRY-PICK, OR TREAT AS A PRODUCT DIRECTION.

Canonical rule:

`one pre-browser package review -> one explicit Approve & Submit -> unattended execution -> internal integrity snapshot -> exactly-once submit -> independent verification -> durable submission record`

Never:

`browser prepares form -> user reviews again -> approve/final submit -> employer`

After the single customer approval, the browser is an internal execution mechanism. A later browser/form review may exist only as cryptographic integrity evidence and must never become a customer-facing approval, Submit, Final Approve, Continue, or resume-browser prompt.

Historical code may remain in Git history only for archaeology, regression proof, deletion, or fail-closing.

The active customer approval authority is `application_execution_mandate_v1`. The old browser-bound `application_single_approval_v1` path is compatibility/history only and must not be routed from Product, API, or deployment code.
