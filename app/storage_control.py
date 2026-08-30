from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT_DIR / "config" / "backup_retention.json"


@dataclass(frozen=True)
class StorageDecision:
    classification: str
    reason: str


def load_retention_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("backup retention policy must be an object")
    return value


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def classify_path(
    path: Path,
    *,
    size_bytes: int | None = None,
    policy: dict[str, Any] | None = None,
    root: Path = ROOT_DIR,
) -> StorageDecision:
    policy = policy or load_retention_policy()
    root = root.resolve()
    resolved = path.resolve(strict=False)
    if not _within(resolved, root):
        return StorageDecision("KEEP", "outside_project_scope")
    relative = resolved.relative_to(root)
    protected = [(root / value).resolve(strict=False) for value in policy.get("protected_paths", [])]
    if any(resolved == value or _within(resolved, value) for value in protected):
        return StorageDecision("KEEP", "explicit_retention_or_active_state")
    if "__pycache__" in relative.parts or resolved.name in {".DS_Store", ".pytest_cache"}:
        return StorageDecision("SAFE_DELETE", "generated_cache")
    cleanup_roots = [(root / value).resolve(strict=False) for value in policy.get("cleanup_roots", [])]
    in_cleanup_root = any(_within(resolved, value) for value in cleanup_roots)
    minimum = int(policy.get("minimum_generated_database_bytes") or 0)
    extensions = {str(value).casefold() for value in policy.get("large_generated_database_extensions", [])}
    actual_size = int(size_bytes if size_bytes is not None else path.stat().st_size)
    normalized_name = resolved.name.casefold()
    generated_database_name = any(
        normalized_name.endswith(extension)
        or f"{extension}." in normalized_name
        for extension in extensions
        if extension
    )
    if in_cleanup_root and generated_database_name and actual_size >= minimum:
        return StorageDecision("SAFE_DELETE", "superseded_large_generated_database_or_dump")
    if in_cleanup_root:
        return StorageDecision("REVIEW_QUARANTINE", "project_backup_not_covered_by_automatic_policy")
    return StorageDecision("KEEP", "active_project_or_uncertain")


def cleanup_candidates(
    *,
    policy: dict[str, Any] | None = None,
    root: Path = ROOT_DIR,
) -> list[dict[str, Any]]:
    policy = policy or load_retention_policy()
    output: list[dict[str, Any]] = []
    for relative_root in policy.get("cleanup_roots", []):
        scan_root = (root / relative_root).resolve(strict=False)
        if not scan_root.is_dir() or not _within(scan_root, root.resolve()):
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            size = path.stat().st_size
            decision = classify_path(path, size_bytes=size, policy=policy, root=root)
            if decision.classification == "SAFE_DELETE" and decision.reason.startswith("superseded_"):
                output.append(
                    {
                        "path": str(path.resolve()),
                        "relative_path": str(path.resolve().relative_to(root.resolve())),
                        "size_bytes": size,
                        "classification": decision.classification,
                        "reason": decision.reason,
                    }
                )
    return sorted(output, key=lambda item: (-int(item["size_bytes"]), str(item["path"])))
