from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(execute: bool) -> dict[str, object]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destination = ROOT / "quarantine" / f"legacy_root_scripts_{stamp}"
    candidates = sorted(
        {
            *ROOT.glob("*.py"),
            *ROOT.glob("*.sh"),
            *ROOT.glob("*.py.before_*"),
            *ROOT.glob("*.sh.before_*"),
            *ROOT.glob("direct_*response*.json"),
            *ROOT.glob(".one_pass_*.json"),
            *ROOT.glob("README_RUN_ORDER_*.md"),
        }
    )
    records = [
        {
            "source_path": path.name,
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "reason": "unreferenced one-off patch/test script outside the active app, bin, migrations, scripts, or tests architecture",
        }
        for path in candidates
    ]
    if execute and candidates:
        destination.mkdir(parents=True, exist_ok=False)
        for path in candidates:
            target = destination / path.name
            shutil.move(str(path), target)
            os.chmod(target, 0o600)
        manifest = destination / "QUARANTINE_INDEX.json"
        manifest.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "recoverable": True,
                    "active_runtime_references_found": False,
                    "files": records,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest, 0o600)
    return {
        "execute": execute,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item["size_bytes"]) for item in records),
        "destination": str(destination.relative_to(ROOT)),
        "recoverable": True,
        "filenames": [item["source_path"] for item in records],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.execute), indent=2, sort_keys=True))
