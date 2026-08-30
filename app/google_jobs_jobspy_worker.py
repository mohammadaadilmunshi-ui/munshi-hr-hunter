from __future__ import annotations

# Compatibility module for old commands. The canonical dashboard adapter is
# now "Google Jobs" and uses the merged SerpAPI -> JobSpy provider chain.
from app.google_jobs_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
