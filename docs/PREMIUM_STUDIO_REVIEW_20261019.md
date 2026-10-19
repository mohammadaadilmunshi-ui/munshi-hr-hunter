# MUNSHI Studio — review and release-readiness report

Historical foundation report for `afaa097`. Its staging/CI limitations were
subsequently addressed; see [the complete runtime review](PREMIUM_STREAMLIT_ACCEPTANCE_20261019.md)
for the later implementation, actual application evidence and current limits.

Status: source review candidate. **Not production validated or deploy-ready.** No push, PR, merge, deployment, service restart, real application, email, or production database operation was performed. No Mac MUNSHI server or worker was started.

## Source authority and production evidence

- Worktree: `/Users/aadil/.codex/worktrees/munshi-premium-20261019`
- Branch: `feat/premium-saas-ux-20261019`
- Verified baseline: `53b9907f1f48ceb9a73acec8d6f9d54085fa95a7`
- Netcup branch: `feat/tsenta-connector-completion-20261015`; checkout clean on initial and final checks.
- The original detached worktree at `0efb` was left untouched. Its older revision was not used for implementation.
- Existing authorized SSH access was used for read-only Git, Docker identity, and source hash checks. The running Netcup Streamlit frontend was inspected through a loopback-only SSH forward; the public logged-out site returns its expected authentication redirect.
- Fifteen frontend/Relay module hashes were compared against the running Hunter image and verified baseline. These include the shell, pages, UI tokens, V2.2 layer, Profile V4, Resume Studio V4/V5/V6, career-quality patch, provider UI, and three Relay dispatch/receipt modules. They matched.

Container identities/start times remained unchanged:

| Service | Container ID prefix | Started at (UTC) |
| --- | --- | --- |
| Hunter | `fa2673e4ba2c` | 2026-10-19 05:51:57 |
| n8n | `cf31faa21d8a` | 2026-10-17 09:06:41 |
| Ollama | `9d3551bf4bf0` | 2026-10-17 09:06:41 |

Hunter image: `sha256:085deeaaabbc34656ccac5fcdad0e43f5fcdf38781d5827131f8ca252daa869f`.

## Design direction and implemented changes

The four installed skills guided diagnosis, visual hierarchy, implementation, and validation. The generic palette suggested by the design search was rejected in favor of the verified MUNSHI identity: forest `#123D31`, brass `#9B6E2D`, cream `#F6F7F4`, ink `#142129`, white surfaces, pastel job cards, Manrope, and the existing crown. No frontend framework or runtime service was added.

The dashboard now opens with a career command center and three navigable tasks. Operational metrics precede filters; filters remain mounted inside a disclosure so their state is retained. Current application activity wraps complete employer/role names and opens the existing workspace. Engine diagnostics sit behind an explicit, lazy disclosure.

The shared system gives jobs, applications, tracker, research, profile, resume, and settings consistent typography, form controls, focus, density, card actions, and responsive spacing. Navigation has an intermediate two-row desktop layout and preserves all destinations and the existing mobile dock. Mobile metrics use two columns, and card actions remain grouped. Dialog scrolling, touch targets, safe-area spacing, and reduced motion are retained/refined.

Resume Studio's appearance controls now follow its title in a preview-only disclosure. Existing keys, generation callbacks, checked one-page generation, and immutable artifact behavior remain. Profile now has an H1 before its subnavigation.

## Root causes addressed

| Evidence | Change | Verification |
| --- | --- | --- |
| Metrics used current Application Center; activity used legacy n8n rows, allowing a misleading empty activity section | One fresh lifecycle projection supplies counters and recent activity | Synthetic current application appears without a legacy result; no invented submission/receipt |
| Closed Streamlit engine expanders still executed observation reads | Stateful expander inside a fragment; read only while open | Closed panel invokes summary zero times; open panel invokes fresh reads |
| Job details relied on an invisible, fixed-height button over card content, with hover help | Visible in-flow details control; help removed | AppTest explicit callback; browser hit-testing confirms title is not covered by a button |
| Mobile filter stack preceded operational content | Metrics and tasks first; filters disclosed | Real baseline and synthetic after screenshots; all viewport checks |
| Resume appearance preceded H1; Profile lacked H1 | Explicit render callback after resume title; shared Profile intro | Generation binding/restoration test and route smoke tests |
| Manrope depended on an external stylesheet/font chain and platform fallbacks varied | Bundle original Manrope Latin/Latin-ext variable fonts under SIL OFL | Font loads with external requests blocked in Chromium and WebKit |

The invisible hit-area and tooltip changes reduce accidental interaction opportunities. They are **not proof** that every reported real-device popup has been reproduced or eliminated. Existing one-shot drawer safeguards were preserved and their regression tests passed.

## Performance evidence and limits

Live baseline, desktop 1440px, one cold Chromium sample over the SSH connection:

| Measurement | Observed |
| --- | --- |
| Time through populated dashboard screenshot capture | 7.591 s |
| Observed LCP | 5.228 s |
| Observed CLS | 0.2247 |
| Browser page exceptions in this sample | 0 |
| Document horizontal overflow at 390px | 0 px |

The 7.591s includes screenshot acquisition after waiting for Reset filters and the data frame. Initial resource observations included a 3.05s main JavaScript resource and 1.335s Material Symbols resource. This is neither a backend profile nor a representative public-network benchmark. No backend latency cause is asserted.

Measured implementation behavior: closed engine-summary calls drop from eager execution to **zero**; one lifecycle read supplies both current counters and recent rows. The external Google Fonts stylesheet dependency is removed. Immutable Studio CSS/font payload is approximately 66KB uncompressed and cached in-process; it increases the inline presentation payload in exchange for removing external font dependencies. Operational data and connection/receipt truth are not cached.

There is **no post-change live LCP/CLS/INP result**: deploying or starting a Mac application runtime was prohibited. Synthetic static-preview timing cannot be compared with live Streamlit timing. The 100ms feedback, 200ms INP, 2.5s LCP, and 0.1 CLS targets remain unproven.

## Tests actually performed

- Production dependency lock installed in an isolated temporary Python 3.12 test environment; `pip check` passes.
- Full test list from Product UI Validation: **237 passed**, one existing FastAPI/Starlette test-client deprecation warning.
- Focused resume presentation, profile authority, session repair, operational metrics, mobile, and career-quality tests: **32 passed**.
- Linux/Docker portability test subset: **45 passed**. This ran on macOS against fixtures; it is not an Ubuntu Docker build.
- Python compilation of `app`, `scripts`, and `integrations`: passed.
- Netcup shadow, container environment, n8n portability, Docker foundation, and Linux compatibility validators: passed.
- Shell syntax for every file in `scripts/netcup/*.sh` and `deploy/netcup/*.sh`: passed.
- Chromium and Playwright WebKit component QA at 375, 390, 430, 768, 1280, 1440, 1920: **14 combinations passed**. Checks include overflow, font availability offline, full long employer names, visible 44px details controls, title hit regions, keyboard menu toggle, two-column phone metrics, reduced motion, and last-control clearance above the dock.
- New synthetic preview test denies database access in a separate process. Existing render-does-not-queue-work and runtime-policy tests passed.
- `git diff --check`: passed. No Relay implementation, worker, schema, dependency lock, deployment transport, Compose layer, or production trigger was modified.

An early test-isolation failure was corrected by running the database-denial import check in its own process. Missing test dependencies/browser binaries were installed from the repository lock/official Playwright downloads, then checks were rerun. Existing checks were not weakened.

## Preview and real rendered screenshots

No local application server is needed:

```sh
python scripts/render_studio_preview.py /tmp/munshi-studio.html
python scripts/verify_studio_browser.py /tmp/munshi-studio-review
```

Open the generated HTML file directly. It uses actual shared render output and styles, synthetic data, and HTML adapters for native Streamlit controls. It is a **component preview**, not a working application, production snapshot, or proof of Streamlit dialog/fragment/browser interactions. Its buttons do not execute actions. The banner identifies it as synthetic.

Local evidence is outside Git to avoid committing production screenshots/private state:

| Evidence | Desktop | Phone |
| --- | --- | --- |
| Actual live Netcup baseline | [1440px](/tmp/munshi-before-desktop.png) | [390px](/tmp/munshi-before-mobile.png) |
| Source-derived synthetic after preview | [Chromium 1440px](/tmp/munshi-studio-review/chromium-1440.png) | [Chromium 390px](/tmp/munshi-studio-review/chromium-390.png) |
| WebKit synthetic after preview | [1440px](/tmp/munshi-studio-review/webkit-1440.png) | [390px](/tmp/munshi-studio-review/webkit-390.png) |

[Open the offline preview](/tmp/munshi-studio-review/preview.html). [Browser result JSON](/tmp/munshi-studio-review/browser-results.json). These `/tmp` artifacts are local and may be removed by normal system cleanup.

Other live routes were visited read-only: Jobs, Applications, Resume Studio, Tracker, Research, Settings, Auto Prepare. Their headings rendered without visible Streamlit exception elements. Profile's H1-based wait timed out because that route had no H1 in the verified source. These heading-visible snapshots were exploratory; they are not full end-to-end completion evidence. One SSH tunnel stalled and was replaced; that timeout is not attributed to the application.

## File map

- `app/product_studio.py`: command center, local font/CSS asset cache, current lifecycle presentation, lazy engine fragment.
- `app/assets/product_studio.css`: shared responsive system, explicit details controls, activity rows, focus/touch states.
- `app/assets/fonts/*`: original Manrope variable font subsets and complete SIL OFL license. Fonts obtained from Google's official font distribution; no font design was modified.
- `app/product_shell.py`: final shared style injection, location context, crown dimensions.
- `app/product_pages.py`: dashboard structure, current application activity, details help removal.
- `app/product_ui.py`: remove external font import.
- `app/profile_workspace_v4.py`: profile H1.
- `app/resume_studio_page_v4.py`, `v5.py`, `v6.py`: optional presentation callback and appearance disclosure; no generation authority changes.
- `scripts/render_studio_preview.py`, `scripts/verify_studio_browser.py`: offline preview and repeatable component QA.
- `tests/test_product_studio.py`, `tests/test_native_resume_studio_canvas_v1.py`: focused regressions and updated presentation-call contract.
- `.github/workflows/product-ui-validation.yml`: include new presentation tests; triggers/deployment behavior unchanged.

## Remaining release gates and rollback

Full acceptance is still pending an independently isolated staging runtime with synthetic fixtures, production actions disabled, and no writable production database. Validate the changed **complete** application across all primary/subroutes, real Streamlit widget/drawer/fragment behavior, scroll/focus retention, dirty form navigation, loading/error/recovery states, resume engine selection, actual document previews/downloads, and Relay receipt projections. No real application/email may be used for that testing.

Run the existing Ubuntu Linux and Docker workflows and x86_64 image build before a deploy-ready claim. They were not dispatched because pushes are prohibited. Deployment Transport Guard was not rerun: deployment transport was unchanged. Actual Safari/iOS devices, assistive technology, light/dark host preference parity, large text/zoom, orientation/keyboard behavior, and full authentication journeys remain untested. MUNSHI's product theme remains light.

The user-supplied older JobSpy runner hash differs from the verified production baseline's `0c2fcdc66ef405407f0b76451f2ac4c8c09c43b011db18bba68aecb2926c7794`. The proven production runner and `python-jobspy==1.1.82` were preserved byte-for-byte; no historical Mac runner was imported. Resolve that documentation discrepancy during release review rather than replacing the verified production runner.

No migration is required. Rollback of this source-only candidate is a normal revert of its commit before promotion. If an eventual explicitly authorized deployment occurs, use the existing authenticated exact-SHA Hunter-only transport and existing image rollback process, retaining all five production Compose layers and the attached approved branch. This assignment did not invoke that process.
