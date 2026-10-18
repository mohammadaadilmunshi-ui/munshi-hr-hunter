# LinkedIn Apply controls and 30-day recovery

LinkedIn discovery and employer application destinations are separate links.
The dedicated ingestion worker now reads explicit Apply controls and the hidden
`applyUrl` field, including LinkedIn offsite redirect wrappers. It keeps ATS
query parameters intact. It never treats a description link or related job as
the application destination and does not click Submit.

Cards label verified destinations by ATS (including Greenhouse, Lever, Ashby,
BambooHR, Paylocity, ADP and UKG). Unknown public employer domains display
“Employer’s website”. Missing destinations display “Employer link pending”; the
LinkedIn discovery source is not mislabeled as the employer ATS.

## Recovery

Run from an authorized host with the application dependencies installed:

```bash
python -m scripts.linkedin_link_recovery_v7 \
  --db /path/to/hunter.db \
  --checkpoint /private/path/linkedin-recovery.json \
  --days 30 --limit 1000 --public-get
```

The database is opened read-only. The checkpoint freezes the cohort and preserves
progress across batches rather than repeatedly inspecting the newest 100 jobs.
Official registered ATS boards are queried with a bounded shared cache. After
five consecutive sign-in walls, LinkedIn requests stop while official-feed
recovery continues. A rate-limit response similarly stops further LinkedIn
requests, including on resume. Unchecked jobs are explicitly labeled separately
from pages actually inspected. Do not repeatedly retry a rate-limited run.

After access is restored, an operator can explicitly retry inaccessible entries:

```bash
python -m scripts.linkedin_link_recovery_v7 \
  --db /path/to/hunter.db \
  --checkpoint /private/path/linkedin-recovery.json \
  --public-get --retry-blocked
```

The CLI uses public requests; it does not extract browser credentials or create a
signed-in session. A public page returning a contextual sign-in modal may show
an external Apply icon while omitting the actual employer URL. A parser cannot
recover a URL absent from the response. Such jobs require authorized signed-in
capture or a matching independently verified employer posting.

## Evidence boundary

Recovery produces unsigned proposals. Existing exact-job Ed25519 signature
verification remains mandatory before `persist_verified_evidence` accepts one.
Direct Apply captures additionally require the page-content digest and explicit
control provenance. Official-feed matches retain the existing exact-employer,
title, location/requisition and strong-description requirements. Recovery never
changes job identity, application state, dispatch claims or submission authority.

The evidence schema is not created by the reader or UI. Provisioning and signed
import are separate, explicit operator operations. This change does not silently
install a migration, signer, scheduler, or bulk submission process.

## October 18 diagnostic result

The rolling 30-day inventory at the frozen diagnostic timestamp contained 891
LinkedIn jobs with no stored Apply URL. Public pages sampled earlier returned
sign-in modals; the recovery request then encountered LinkedIn rate limiting.
Official feeds produced two strongly matching proposals, on Ashby and Greenhouse.
The other 889 destinations were not recovered by that scan. No database rows or
application queues were written during the diagnostic.

The source patch does not by itself restore access to sign-in-gated LinkedIn
URLs. Do not report the backlog as recovered or an unsigned proposal as an
executable application destination.
