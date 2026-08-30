from __future__ import annotations

import sys

from app.hourly_worker import main


def _has_argument(name: str) -> bool:
    return any(
        argument == name or argument.startswith(name + "=")
        for argument in sys.argv[1:]
    )


if __name__ == "__main__":
    # Independent V4 workers own Google, Indeed, and LinkedIn.
    # The legacy/general JobSpy route is ZipRecruiter-only to prevent the same
    # boards from being collected twice.
    if not _has_argument("--sites"):
        sys.argv.extend(["--sites", "zip_recruiter"])
    if not _has_argument("--results"):
        sys.argv.extend(["--results", "25"])
    if not _has_argument("--hours-old"):
        sys.argv.extend(["--hours-old", "168"])
    main()
