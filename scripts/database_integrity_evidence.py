#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HUNTER_DB = ROOT / "data" / "hunter.db"
N8N_DB = Path.home() / ".n8n" / "database.sqlite"


def _check(path: Path, *, full: bool) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0]) if full else "not requested"
    foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    connection.close()
    return {
        "path": str(path),
        "quick_check": quick,
        "integrity_check": integrity,
        "foreign_key_violations": foreign,
        "size_bytes": path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = {
        "checked_at_system_local": datetime.now().astimezone().isoformat(),
        "hunter": _check(HUNTER_DB, full=True),
        "n8n_read_only": _check(N8N_DB, full=False),
        "secrets_included": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    hunter = payload["hunter"]
    return 0 if hunter["quick_check"] == hunter["integrity_check"] == "ok" and hunter["foreign_key_violations"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
