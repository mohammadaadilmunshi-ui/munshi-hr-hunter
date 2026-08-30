from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.storage_control import cleanup_candidates, load_retention_policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retention-aware HR Hunter backup cleanup")
    parser.add_argument("--execute", action="store_true", help="Unlink only SAFE_DELETE inventory entries")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    policy = load_retention_policy()
    candidates = cleanup_candidates(policy=policy)
    before = shutil.disk_usage(ROOT_DIR).free
    removed: list[dict[str, object]] = []
    if args.execute:
        for item in candidates:
            path = Path(str(item["path"]))
            if path.is_symlink() or not path.is_file():
                continue
            path.unlink()
            removed.append(item)
    after = shutil.disk_usage(ROOT_DIR).free
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp_utc": timestamp,
        "mode": "execute" if args.execute else "dry_run",
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(item["size_bytes"]) for item in candidates),
        "removed_count": len(removed),
        "removed_bytes": sum(int(item["size_bytes"]) for item in removed),
        "free_space_before_bytes": before,
        "free_space_after_bytes": after,
        "free_space_delta_bytes": after - before,
        "retained_paths": policy.get("protected_paths", []),
        "candidates": candidates,
    }
    report_path = ROOT_DIR / "reports" / f"storage_cleanup_inventory_{timestamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "timestamp_utc", "mode", "candidate_count", "candidate_bytes",
                    "removed_count", "removed_bytes", "free_space_before_bytes",
                    "free_space_after_bytes", "free_space_delta_bytes",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
