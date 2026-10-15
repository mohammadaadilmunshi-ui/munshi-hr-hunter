# PR #103: real LinkedIn to employer links — predeployment pilot

Status: read-only local test. No production changes, no employer application submitted.

## What is proven

- Exact stored LinkedIn job URL can be passed to the single-job CLI
  scripts/linkedin_employer_link_pilot_v6.py.
- CLI reads a Hunter SQLite database in mode=ro and uses the existing healthy
  employer ATS board registry. It only issues HTTPS GET requests when
  --public-ats-get is explicitly supplied. It does not click LinkedIn Apply,
  and it does not write link evidence, change jobs, or submit applications.
- Figma HRIS Analyst:
  LinkedIn https://www.linkedin.com/jobs/view/4469050289
  Employer https://boards.greenhouse.io/figma/jobs/6201385004?gh_jid=6201385004
  Followed real HTTPS redirect to job-boards.greenhouse.io, final HTTP 200,
  job ID and role visible.
- Duolingo Technical Recruiter:
  LinkedIn https://www.linkedin.com/jobs/view/4454093526
  Employer https://careers.duolingo.com/jobs/8708178002?gh_jid=8708178002
  Employer URL responded HTTP 200. Job ID was not present in initial rendered
  HTML; official public Greenhouse feed corroborated the job.
- OpenAI Internal Communications, Enterprise & Growth:
  Employer https://jobs.ashbyhq.com/OpenAI/dcbb011b-bc0f-41e6-b3c6-34068d177e54/application
  Exact LinkedIn title, normalized location, high description agreement.
- Perplexity Technical Sourcer, AI:
  Employer https://jobs.ashbyhq.com/Perplexity/3d7ef710-b1f6-4a80-af57-946f9bccd35b/application
  Exact LinkedIn title, normalized location, high description agreement.

A 13-employer (one job per board) public ATS GET audit initially found 2 verified,
11 unknown. Diagnostics found ATS "San Francisco" versus LinkedIn "San Francisco,
CA" caused false negatives. The conservative rule now normalizes only a short,
unambiguous city mapping. Two more jobs now verify. Notion and Harvey still
have genuinely different cities; they remain unverified. Other jobs were not
on current employer job boards, or had insufficient evidence.

## How to reproduce ONE job safely

    python -m scripts.linkedin_employer_link_pilot_v6       --db /path/to/read_only_copy/hunter.db       --linkedin-url https://www.linkedin.com/jobs/view/4469050289       --public-ats-get

This only works for jobs already saved in the supplied Hunter DB and for
companies that have a known, healthy employer ATS board. It does not
automatically discover unknown employer websites and cannot reproduce the
exact LinkedIn Apply-click tracking redirect.

## Release blockers

- Most LinkedIn records are missing signed/vetted employer board identities;
  this pilot alone does not cover all saved jobs.
- No V5 evidence migration or signer-trust provisioning has run in production.
- The dashboard can only show the separate verified employer link after
  properly signed evidence is persisted.
- Production trust roots, authorized signing service, audit ledger, and
  application-level access control are not yet configured.
- Need actual staging deployed UI/DB integration smoke and rollback test,
  then a reviewed production promotion.
- Prepare Application end-to-end remains separately unverified.
- Production must never receive untrusted user-supplied public trust keys or
  unsigned candidates.

Current repository contains read-only code and tests only. No deployment.
