"""Fail-closed public checkout scanner, including historical Git blobs.

The deployment credential secret *names* in GitHub workflows are allowed.
Actual credential material is not. Known fake tokens in unit tests are
allowlisted only by their exact SHA-256 digest and exact test filename.

The scanner is a defense-in-depth control, not proof of zero possible exposure.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import BinaryIO

MAX_BLOB_BYTES = 1_048_576
PATTERNS = {
    "private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    ),
    "github_token": re.compile(rb"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{22,}\b"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "openai_key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{25,}\b"),
    "google_api_key": re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b"),
    "slack_key": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{25,}\b"),
    "stripe_secret": re.compile(rb"\bsk_live_[A-Za-z0-9]{20,}\b"),
}

# Only synthetic tokens that were already publicly committed as test inputs.
SYNTHETIC_TEST_FIXTURES = {
    "tests/test_native_resume_service_v2.py": frozenset({
        "11454425443681917aff6001226549cc04e63ceda97a488b72106d8a0b961bb4",
        "526fd1ceb89406207e7a440cf331efb4914bd57605386bce6d4567a6a1817a2e",
        "25927638492754cbabcd0f8221d650c803d6f59dd7e6f1bbd2199c38e558f84a",
    }),
    "tests/test_phase45_truth_binding.py": frozenset({
        "90bd50c93b6540c86f9421849b40bc90ad6547622be6ce17deb30d51364a944f",
    }),
    "tests/test_secure_api_vault.py": frozenset({
        "cc837d7cd08b90832d8aa8ea067b96fa5e6daef6a4eaa2475b3efdd361722746",
    }),
}


def check_blob(path: str, data: bytes) -> list[str]:
    """Return detection names only. Never log source text or secret values."""
    found: list[str] = []
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(data):
            digest = hashlib.sha256(match.group()).hexdigest()
            if digest not in SYNTHETIC_TEST_FIXTURES.get(path, frozenset()):
                found.append(label)
    return sorted(set(found))


def forbidden_path(path: str) -> bool:
    name = Path(path).name.casefold()
    if path in {".env.example", ".env.netcup.shadow.example"}:
        return False
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return True
    if name.endswith((".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm",
                      ".pem", ".p12", ".pfx", ".pkcs12")):
        return True
    return name in {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"}


def _exact_read(stream: BinaryIO, size: int) -> bytes:
    parts: list[bytes] = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise RuntimeError("Git object stream ended unexpectedly")
        parts.append(chunk)
        size -= len(chunk)
    return b"".join(parts)


def check_git_history(root: Path) -> tuple[int, int]:
    """Scan all available Git object refs. CI must use full-history checkout."""
    if subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
        text=True,
    ).strip() != "false":
        raise RuntimeError("Refuse incomplete Git history: checkout fetch-depth must be 0")

    objects = subprocess.check_output(
        ["git", "-C", str(root), "rev-list", "--objects", "--all"], text=True,
    ).splitlines()
    names: dict[str, str] = {}
    for line in objects:
        sha, _, path = line.partition(" ")
        if path and sha not in names:
            names[sha] = path

    checked = subprocess.run(
        ["git", "-C", str(root), "cat-file",
         "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input="".join(f"{sha}\n" for sha in names),
        capture_output=True, check=True, text=True,
    )
    blobs = []
    for row in checked.stdout.splitlines():
        parts = row.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        sha, _, size_str = parts
        size = int(size_str)
        if size > MAX_BLOB_BYTES:
            if forbidden_path(names[sha]):
                raise RuntimeError("Oversize potentially sensitive historical file")
            continue
        blobs.append((sha, names[sha]))

    proc = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Git content reader failed to start")
    scanned = 0
    try:
        for sha, name in blobs:
            proc.stdin.write((sha+"\n").encode())
            header = proc.stdout.readline().split()
            if len(header) != 3 or header[0].decode() != sha:
                raise RuntimeError("Unexpected Git object header")
            content = _exact_read(proc.stdout, int(header[2]))
            if proc.stdout.read(1) != b"\n":
                raise RuntimeError("Malformed Git content framing")
            if forbidden_path(name) or check_blob(name, content):
                # Only output the filename and category, never token material.
                print("PUBLIC_RELEASE_BLOCKED historical tracked content:", name)
                raise RuntimeError("Historical credential indicator detected")
            scanned += 1
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)
    return scanned, len(names)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z"],
    ).split(b"\x00")
    for raw in files:
        if not raw:
            continue
        name = raw.decode("utf-8", "surrogateescape")
        if forbidden_path(name):
            print("PUBLIC_RELEASE_BLOCKED tracked filename:", name)
            return 1
        content = (root / name).read_bytes()
        if check_blob(name, content):
            print("PUBLIC_RELEASE_BLOCKED tracked credential indicator:", name)
            return 1
    try:
        scanned, count = check_git_history(root)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("PUBLIC_RELEASE_BLOCKED", type(error).__name__)
        return 1
    print("PUBLIC_REPOSITORY_SECRET_PATTERN_SCAN=PASS")
    print("GIT_HISTORY_BLOBS_SCANNED=", scanned, "OBJECT_PATHS=", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
