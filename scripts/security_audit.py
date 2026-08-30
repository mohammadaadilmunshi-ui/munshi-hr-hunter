from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {
    ".git", ".venv", ".venv_runtime", "tools", "backups", "patch_backups",
    "rollback", "quarantine", "logs", "reports", "diagnostics", "data",
    "__pycache__", ".pytest_cache",
}
TEXT_SUFFIXES = {".py", ".sh", ".json", ".toml", ".yaml", ".yml", ".plist", ".md"}
IDENTIFIER_RE = re.compile(
    r"(?i)\b(?P<key>[A-Za-z_][A-Za-z0-9_]*(?:token|secret|password|api[_-]?key|webhook)[A-Za-z0-9_]*)\b\s*[:=]\s*(?P<quote>['\"])(?P<value>[^'\"\r\n]{8,})(?P=quote)"
)
PLACEHOLDERS = {"changeme", "replace-me", "example", "placeholder", "your-token", "your-secret"}


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def audit() -> dict[str, object]:
    sensitive_files: list[dict[str, object]] = []
    env_files = [ROOT / ".env", *ROOT.glob("**/.env.before_*")]
    for path in sorted({item.resolve() for item in env_files if item.is_file()}):
        keys: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line.strip())
            if match:
                keys.append(match.group(1))
        sensitive_files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "mode": _mode(path),
                "secure_permissions": (path.stat().st_mode & 0o077) == 0,
                "key_names": sorted(keys),
                "value_count": len(keys),
                "values_redacted": True,
            }
        )

    literal_findings: list[dict[str, object]] = []
    scanned = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES or path.stat().st_size > 5 * 1024 * 1024:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in IDENTIFIER_RE.finditer(line):
                value = match.group("value").strip()
                if (
                    value.casefold() in PLACEHOLDERS
                    or "fixture" in value.casefold()
                    or value.casefold().endswith(".test")
                    or "${" in value
                    or "os.getenv" in line
                ):
                    continue
                literal_findings.append(
                    {
                        "path": str(path.relative_to(ROOT)),
                        "line": line_number,
                        "identifier": match.group("key"),
                        "value_sha256_prefix": _fingerprint(value),
                        "value_length": len(value),
                        "value_redacted": True,
                    }
                )
    return {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "files_scanned": scanned,
        "sensitive_files": sensitive_files,
        "hardcoded_secret_literal_findings": literal_findings,
        "finding_count": len(literal_findings),
        "values_printed": False,
        "n8n_credentials_modified": False,
    }


if __name__ == "__main__":
    result = audit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = ROOT / "reports" / f"AADIL_HR_HUNTER_SECURITY_AUDIT_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    print(json.dumps({"report": str(output.relative_to(ROOT)), "files_scanned": result["files_scanned"], "finding_count": result["finding_count"], "values_printed": False}, indent=2))
