# HISTORICAL ONLY: legacy second-approval / browser-babysitting flow

Status: RETIRED. DO NOT REUSE, REVIVE, CHERRY-PICK, OR TREAT AS A PRODUCT DIRECTION.

## Canonical MUNSHI invariant

MUNSHI is an asynchronous career application operating system. The browser is an internal execution mechanism, not the product surface.

The supported customer flow is:

1. MUNSHI prepares the complete application package in the background.
2. NEEDS_INPUT is used only when genuinely necessary.
3. The user reviews the completed package once.
4. The user performs one explicit `Approve & Submit` action.
5. MUNSHI executes unattended after that approval.
6. MUNSHI independently verifies submission and writes a durable receipt / CRM transition.

## Explicitly retired flow

Any implementation or UX that does any of the following after the user has already approved is historical-only and must not be considered for future implementation work:

- opens a browser for the user to watch or operate;
- asks for a second approval;
- waits for the user at a final confirmation step;
- asks the user to resume or continue browser execution;
- exposes a second `Submit`, `Final Approve`, `Continue`, or equivalent irreversible-action confirmation;
- treats the browser session itself as the customer-facing product.

Canonical rule:

`one package review -> one explicit Approve & Submit -> unattended execution -> independent verification -> durable submission record`

Never:

`review -> approve -> browser opens -> wait for user -> second/final confirmation -> submit`

## Search / archaeology rule for future agents

If code, docs, tests, branches, commits, handoffs, or generated runtime artifacts describe the retired second-approval/browser-assisted flow, treat them as HISTORICAL ONLY.

Do not spend implementation time evaluating, modernizing, or cherry-picking those paths unless the task is specifically one of:

- regression/security archaeology;
- proving that the retired path cannot be reached;
- deleting or fail-closing the retired path;
- understanding an old migration or compatibility boundary.

Do not use historical second-approval artifacts as architectural precedent.

Preserve useful low-level browser/runtime mechanics only when they fit the canonical one-review -> unattended-execution contract.

## Authority boundary

The single customer approval is owned by Hunter. After that approval, downstream Apply/browser execution must consume canonical authority and must not create a second customer-facing approval gate.

This tombstone is architectural, not a request to delete Git history.