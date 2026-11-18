# MUNSHI Product VNext — integrated redesign and staged release

Date: 2026-10-16. Target: original dashboard, not a staging lookalike.

## Product identity
Preserve the crown, Manrope, forest green #123D31, ivory #F6F7F4, and brass #9B6E2D. Keep the existing Streamlit app and canonical backend. Do not create a parallel mobile database or application worker.

## Implemented in the first VNext UI branch
- An accessible, responsive five-tab mobile bottom navigation (Home, Jobs, Applications, Prepare, Tracker). Existing More menu retains Profile, Resume Studio, Research and Settings.
- A progressive responsive layout for phone/tablet/desktop, accessible touch targets, overflowing tables, dialog sizes, metrics, and compact application cards.
- Allowlisted page/job links, application breadcrumbs, job details, profile shortcut, saved ATS link fallback.
- Customer-facing application stages that cannot claim employer verified without receipt evidence.
- Clear next-action text for preparing, applying and review states; a manual live Application Center refresh.
- Discovery volume accurately labelled separately from actual submission authority.
- Correct Tsenta OAuth/profile/dispatcher status copy, stacked on the tested earlier Tsenta UI fix.
- Warnings for old failed applications: reserving a provider does not restart or submit.

## Measured QA
- Python accessibility/navigation/status contract tests.
- Controlled Playwright at 360, 390, 430, 768, 1024, 1440 px with original CSS loaded: no full-page horizontal overflow; mobile dock appears only at <=920px, 1 active tab, >=48px touch buttons on phones.
- Full Hunter regression suite on exact branch is the release gate; controlled-browser tests do not replace live iPhone or Android testing.

## Backend wiring still required before fully unattended application claims
| Area | Remaining acceptance |
| --- | --- |
| Native preparation | Fresh eligible job to truthful Candidate Truth, exact job/ATS/resume plan, full browser form and frozen review, no unsafe stale replay |
| LinkedIn two links | Guarded signed-evidence migration PR #107, independently proven exact-employer ATS apply link backfill, no fake links |
| Tsenta | OAuth and profile verified, dispatcher still unvalidated; verify scoped dispatch with true backend evidence before enabling |
| Submit and receipt | One-use owner-scoped authorization, full form/document validation, independent employer receipt before Submitted/Verified |
| Application Center | Durable status freshness, next genuine action, no misleading automation badge |
| Edge extension | One backend/ledger and no parallel autonomous engine |
| Mobile | Physical iOS Safari and Android Chrome tests, login, keyboard viewport, drawers, ATS challenge flows, accessibility |

## Release gates
1. Review/merge Tsenta wording PR #108 and VNext changes in order, reconcile with evidence migration PR #107.
2. Pass exact-SHA CI and desktop/tablet/mobile visual tests.
3. Perform read-only production snapshot: Hunter/Apply SHAs, DB quick check, protected OAuth presence, worker status, current application and receipt counters.
4. Use guarded exact-SHA storage-safe Netcup Hunter release. Do not hot-edit live containers or restart the Submit worker as part of a presentation release.
5. Verify real dashboard routes, correct Tsenta status, job links, application state and API health; preserve application/receipt rows.
6. Only then perform one explicitly authorized native ATS canary through frozen review -> one-use submit -> independently verified receipt.
7. Preserve rollback data and audit history.

This branch is a source change only; it does not deploy production or claim new job submissions.
