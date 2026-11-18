from __future__ import annotations

# Compatibility module for old commands. The canonical dashboard adapter is
# now "Google Jobs" and uses free JobSpy Google discovery only.
from app.google_jobs_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
