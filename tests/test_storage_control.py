from __future__ import annotations

from pathlib import Path

from app.storage_control import classify_path


def _policy():
    return {
        "cleanup_roots": ["backups", "patch_backups"],
        "protected_paths": ["data/hunter.db", ".env", "app", "config", "rollback", "backups/retained.sqlite"],
        "large_generated_database_extensions": [".sqlite", ".db", ".sql"],
        "minimum_generated_database_bytes": 100,
    }


def test_active_database_and_source_are_never_deletable(tmp_path: Path) -> None:
    assert classify_path(tmp_path / "data/hunter.db", size_bytes=1000, policy=_policy(), root=tmp_path).classification == "KEEP"
    assert classify_path(tmp_path / "app/targeting.py", size_bytes=1000, policy=_policy(), root=tmp_path).classification == "KEEP"


def test_personal_or_external_path_is_never_deletable(tmp_path: Path) -> None:
    external = tmp_path.parent / "personal_resume.pdf"
    assert classify_path(external, size_bytes=1000, policy=_policy(), root=tmp_path).classification == "KEEP"


def test_only_unretained_large_generated_backup_is_safe_delete(tmp_path: Path) -> None:
    deleted = classify_path(tmp_path / "backups/old.sqlite", size_bytes=1000, policy=_policy(), root=tmp_path)
    retained = classify_path(tmp_path / "backups/retained.sqlite", size_bytes=1000, policy=_policy(), root=tmp_path)
    unknown = classify_path(tmp_path / "backups/unique.zip", size_bytes=1000, policy=_policy(), root=tmp_path)
    assert deleted.classification == "SAFE_DELETE"
    assert retained.classification == "KEEP"
    assert unknown.classification == "REVIEW_QUARANTINE"


def test_generated_database_backup_suffix_is_classified(tmp_path: Path) -> None:
    decision = classify_path(
        tmp_path / "patch_backups/old/database.sqlite.before",
        size_bytes=1000,
        policy=_policy(),
        root=tmp_path,
    )
    assert decision.classification == "SAFE_DELETE"
