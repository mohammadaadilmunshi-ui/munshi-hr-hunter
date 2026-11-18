"""Compatibility shim for the Docker-safe recovery entrypoint."""

from app.linkedin_link_recovery_v7 import recovery_main

main = recovery_main

if __name__ == "__main__":
    raise SystemExit(main())
