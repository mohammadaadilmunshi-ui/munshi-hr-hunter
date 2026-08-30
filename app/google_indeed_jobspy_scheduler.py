"""Compatibility wrapper for the V2 scheduler module name."""
from app.jobspy_board_scheduler import main

if __name__ == "__main__":
    raise SystemExit(main())
