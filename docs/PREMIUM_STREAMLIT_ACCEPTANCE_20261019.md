# MUNSHI: complete Streamlit redesign and acceptance evidence

Review candidate on `feat/premium-saas-ux-20261019`, continuing `afaa097`.
[Draft PR #142](https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter/pull/142)
contains the implementation and current exact-head CI checks. **No production
deployment, merge, real submission, email, or production-data mutation.** The Mac
was used for source work, offline tests, browsers and SSH—not an application server.

## Source and production boundary

Netcup remained at `53b9907f1f48ceb9a73acec8d6f9d54085fa95a7`, attached to
`feat/tsenta-connector-completion-20261015`, with a clean checkout. The isolated
feature branch descends from that commit; stale `main` and the older `0efb`
worktree were not imported or overwritten. Fifteen frontend/Relay source hashes
matched the deployed image and verified baseline during diagnosis.

Read-only rechecks preserved these production identities and start times:

| Service | Container prefix | Started (UTC) |
| --- | --- | --- |
| Hunter | `fa2673e4ba2c` | 2026-10-19 05:51:57 |
| n8n | `cf31faa21d8a` | 2026-10-17 09:06:41 |
| Ollama | `9d3551bf4bf0` | 2026-10-17 09:06:41 |

Production Hunter image: `sha256:085deeaaabbc34656ccac5fcdad0e43f5fcdf38781d5827131f8ca252daa869f`.
The public dashboard continues to return its authentication redirect. No production
Compose layer, worker, Relay implementation, receipt authority, dependency lock,
credential, database, or deployment transport was changed.

## Product transformation

The four requested skills guided the work throughout. The original crown,
Manrope, forest, brass, cream and pastel opportunity cards remain. The design
uses a quieter shared surface system, deliberate typography, readable evidence,
visible actions, consistent touch targets and native interaction feedback.

- Dashboard: career command center, useful shortcuts, operational metrics before
  secondary controls, current lifecycle activity, and lazy engine observations.
- Jobs: search and results first; all other filters in one persistent disclosure
  with active count. Complete employer/role names wrap. Explicit details controls
  replace invisible card-sized hit areas. The initial batch is 24 instead of 48;
  incremental loading and the previous 2,880-card browsing capacity remain.
- Applications, Tracker, Research, Auto Prepare and Settings share the same
  navigation, hierarchy, forms, status language, spacing and responsive controls.
  Tracker and Research now preserve shareable subroutes.
- Profile: genuine first-use upload/paste intake, explicit source confirmation,
  existing local evidence extraction and separate permanent-profile confirmation.
  Inert future-profile controls no longer push working tabs off a phone screen.
- Resume Studio: title before optional appearance controls, truthful preview-only
  labeling, visible source-paper reconstruction and the existing checked-artifact
  authority. No fabricated PDF/download or submission-success claim.
- Mobile: compact five-destination dock, working overflow menu, safe-area and
  scroll clearance, explicit drawer dismissal, readable fields, and no ordinary
  card-title tap opening a dialog.

## Diagnosed causes and fixes

| Observed cause | Generalized fix | Evidence |
| --- | --- | --- |
| Ordinary links reloaded the entire browser document/session | Native allowlisted navigation callbacks; query URL retained | Browser document sentinel survives navigation |
| Every job save/filter rebuilt shell and unrelated monitors | Browse fragment; smaller initial render | Save/search/filter: zero full-page reruns; deep-list scroll delta 0px |
| Closed engine disclosure still performed observation reads | Read only inside the opened fragment | Closed: zero summary reads; opening: fresh observations, no full rerun |
| Production image omitted `.streamlit` theme; layered late CSS shifted placeholders | Package theme with image, normalize one style payload, one-shot initial hydration reveal | Real before/after CLS observations; no layout slots for CSS |
| Legacy mobile column rule stacked the native dock | More specific shared dock layout; decorative CSS vector icons | Five aligned 44px+ targets; dock under 90px at normal text size |
| Icon text polluted native button names | Decorative icons removed from accessible button names | Actual Home/Jobs taps work by their visible accessible names |
| Profile source intake pointed to a Studio without an intake form | Restore intake in Profile through the existing source API | Real upload → review → save → build → explicit confirm → visible paper |
| Preview toggles requested an extra whole-app rerun | Native callbacks update state before rendering | Actual open/close of the paper preview |
| Low-contrast small brass/placeholder text | Darker text-only tones within the existing palette | Sampled tokens meet 4.5:1; focus/touch/reduced-motion checks |

An initial `st.page_link` implementation failed against the single-entrypoint
runtime and was replaced with supported callbacks. Real browser tests also caught
the stacked dock, startup skeleton shifts, missing subroute updates, and absent
staging encryption. These failures were diagnosed, not suppressed. Staging now
uses its own ephemeral AES-GCM key; production keys were never accessed.

## Actual application testing

The [staging runtime](../deploy/uiux/README.md) runs the complete Streamlit app
against 99 fictional jobs and four synthetic application states. It has no
production mounts, `.env`, credentials, API service, workers or public endpoint.
Its root filesystem is read-only, capabilities dropped, network internal, and
data/state/key stored only in independent tmpfs. Additional Python and SQLite
guards deny external execution and operational queue writes.

After real interactions, integrity was `ok` and all five inspected dispatch,
submission, preparation, Telegram and notification outboxes/queues contained
zero records. The four seeded lifecycle fixtures never claim a verified receipt.
Synthetic job/profile/preference edits are intentionally enabled; production
database writes are not.

Browser coverage includes all nine routes at 1440px and 390px in Chromium and
Playwright WebKit, plus Dashboard/Jobs/Profile at 375, 430, 768, 1280 and 1920px.
Checks exercise actual taps, menus, native navigation, save, committed search,
filter persistence/reset, details, Escape, focus, application selection/search/
close, Tracker inbox/pipeline, Research sections, Settings save/sections, source
upload/paste, local profile building, confirmation, paper rendering and appearance.
Landscape, reduced motion, dark host preference with the intentional light theme,
and verified 32px root-text scaling are also covered. Enlarged-text dock clearance
is checked separately from normal-text dock height.

The browser script refuses clicks without the synthetic-only marker. It checks
actual crown-image and Manrope loading with external HTTPS blocked. Paper-preview
evidence is captured from the rendered iframe, not just an iframe DOM node.

## Performance: measured, not inferred

Reproducible controlled comparison:
[Linux browser run 39261940731](https://github.com/mohammadaadilmunshi-ui/munshi-hr-hunter/actions/runs/39261940731)
at `491ac66`. Both pinned baseline and candidate were built on the same disposable
Ubuntu x86_64 runner and exercised sequentially with the same fixtures. These are
medians across nine different route samples per browser/viewport, not field
percentiles or repeated samples of one route. The first route includes cold
imports; later routes benefit from module/browser caches. Raw per-route results
and real screenshots are in the run's `synthetic-streamlit-browser-evidence`
artifact. Current-head repetitions are linked from the PR checks.

| Engine / width | Ready: before → after | LCP: before → after | Maximum observed CLS: before → after |
| --- | --- | --- | --- |
| Chromium 1440 | 0.753s → 0.809s | 620ms → 748ms | 0.205 → 0.000028 |
| Chromium 390 | 0.841s → 0.812s | 624ms → 748ms | 0.316 → 0.000206 |
| WebKit 1440 | 1.303s → 1.276s | 1044ms → 1278ms | unavailable |
| WebKit 390 | 1.266s → 1.162s | 876ms → 1141ms | unavailable |

Job-list server render: 1.1443s → 0.5991s in desktop Chromium; 1.3386s → 0.5702s
on phone Chromium. Native navigation completed in 0.841s with the same document;
deep-list save completed in 0.558s, with zero full-page reruns and 0px scroll change.
Sampled Chromium Event Timing entries were 16–32ms. **These are not field INP or
proof that every backend operation completes within 100–200ms.**

Initial readiness is broadly comparable, not uniformly faster. LCP is slightly
later because the candidate reveals a usable hydrated frame rather than showing
partial unthemed placeholders. All 36 LCP samples in this Linux run were below
2.5s, but that is not a production-connection SLO proof. A separate real Netcup
staging sweep over SSH measured median readiness of 3.674s desktop / 4.303s phone
in Chromium, and 3.811s / 4.094s in WebKit. LCP there exceeded 2.5s; WebKit cold
loads remained slower than the baseline. Desktop Chromium maximum CLS was 0.0142;
phone maximum was 0.000061. No passing connection-performance claim is made.

WebKit in this environment does not expose Layout Instability entries. Its old
raw zero values must not be read as passing CLS; the final instrument explicitly
records unsupported metrics as `null`. CLS uses the maximum session window and
also retains the raw shift sum. No cache contains live operational readiness.

## Real screenshots

These are actual Streamlit renders with fictional data—not static component
previews, stock images, production applicant data or invented screenshots. The
before app uses the verified Netcup production image in isolated staging. The
after app uses the redesigned production-derived source in isolated staging.
Screenshots are viewport captures; Streamlit's inner scrolling is not mislabeled
as a full-page capture. Full browser/width evidence is also available in CI.

| Surface | Before | After |
| --- | --- | --- |
| Desktop dashboard | [Before](uiux-screenshots/20261019/before-desktop.png) | [After](uiux-screenshots/20261019/after-desktop.png) |
| Phone dashboard | [Before](uiux-screenshots/20261019/before-mobile.png) | [After](uiux-screenshots/20261019/after-mobile.png) |
| Phone jobs | [Before](uiux-screenshots/20261019/before-mobile-jobs.png) | [After](uiux-screenshots/20261019/after-mobile-jobs.png) |
| Desktop Profile intake | [Before](uiux-screenshots/20261019/before-profile.png) | [After](uiux-screenshots/20261019/after-profile.png) |

[Rendered synthetic resume paper](uiux-screenshots/20261019/resume-paper.png)
and [working appearance controls](uiux-screenshots/20261019/resume-appearance.png).

## Validation and source map

Offline compilation, focused UI regressions, **816 passing tests** in the relevant
workflow union, dependency consistency, Docker/Netcup/container/n8n portability validators,
Linux audit, shell syntax and diff checks were run. Current exact-head GitHub
checks cover Linux, Ubuntu Docker build, foundation/JobSpy contracts, transport
guard, repository safety, profile/resume, vault, application boundaries, n8n and
the full baseline/candidate browser test. See the PR for definitive check status.

Native Netcup staging build inspection proved Linux x86_64,
`python-jobspy==1.1.82`, and runner SHA-256
`0c2fcdc66ef405407f0b76451f2ac4c8c09c43b011db18bba68aecb2926c7794`.
That is the verified production contract; the older hash in the initial task was
not copied over it. Resolve that documentation discrepancy before promotion.

Changed areas are reviewable in the PR:

- `app/product_studio.py`, `app/assets/product_studio.css`, local licensed fonts:
  shared design system, command center, lifecycle presentation, lazy engine panel.
- `app/product_navigation.py`, `product_shell.py`, `product_pages.py`,
  `unified_job_workspaces_v1.py`, `career_os_quality_patch_v1.py`: native navigation,
  subroutes, progressive filters, browse fragment and batching.
- Profile and Master Resume UI modules: intake, accessible visible navigation,
  preview callbacks; existing source/extraction/confirmation contracts retained.
- Resume Studio V4/V5/V6: title-first presentation callback and appearance disclosure.
- `scripts/package_streamlit_theme.py`, `Dockerfile`, `.dockerignore`: packaged
  first-render theme and one-shot hydration reveal; no runtime framework patch.
- `deploy/uiux/*`, stage/guard/browser scripts, focused tests and UI workflows:
  isolated complete-app verification and repeatable evidence.

## Remaining release gates

This is **review-ready, not an authorization to promote production**. Actual
Safari/iOS devices, VoiceOver/screen-reader certification, native mobile keyboard
behavior and production authenticated-ingress field performance remain unproven.
Playwright WebKit is not real-device Safari. Root-text scaling is not OS Dynamic
Type or a substitute for a device check. The product intentionally remains light.

External AI generation, real Relay submissions, real email/OAuth and live receipt
reconciliation were not executed. Their existing fixture-driven compatibility
tests passed; production operational contracts were not rewritten. Resume-paper
reconstruction is not proof of a newly generated, checked downloadable artifact.
The existing preview-only versus immutable-download limitation remains explicit.

No schema migration is required. Source rollback is a revert of the feature
changes, including rebuilding the prior theme/image. Staging is independently
disposable. Any later production promotion still requires independent review,
an approved exact SHA, the authenticated restricted deployment path, all five
Compose layers, unchanged n8n/Ollama identities, and the existing rollback gates.
No such promotion was invoked here.
