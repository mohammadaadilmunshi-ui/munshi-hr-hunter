#!/usr/bin/env python3
"""Non-secret readiness verifier for MUNSHI encrypted credential storage."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# When executed as ``python scripts/verify_secure_vault.py``, Python places the
# scripts directory (not the repository root) on sys.path. Add the repository
# root explicitly so this verifier behaves the same locally, in CI, and inside
# the Hunter container.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import initialize_database
from app.secure_vault import vault_readiness


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-production",
        action="store_true",
        help="Fail unless the vault satisfies production key-source policy.",
    )
    args = parser.parse_args()

    initialize_database()
    readiness = vault_readiness()
    public = {
        "available": bool(readiness.get("available")),
        "production_ready": bool(readiness.get("production_ready")),
        "key_source": str(readiness.get("key_source") or "none"),
        "algorithm": str(readiness.get("algorithm") or "unknown"),
        "secret_count": int(readiness.get("secret_count") or 0),
        "reason": str(readiness.get("reason") or "unknown"),
    }
    print(json.dumps(public, sort_keys=True))

    if not public["available"]:
        return 2
    if args.require_production and not public["production_ready"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
