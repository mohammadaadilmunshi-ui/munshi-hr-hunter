#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOME = Path.home()
PROJECT = HOME / "Aadil-HR-Hunter"

ENV_FILES = (
    PROJECT / ".runtime" / "n8n_runtime.env",
    PROJECT / ".env",
    HOME / ".n8n" / ".env",
    HOME / ".env",
    HOME / ".aadil_hr_hunter_secrets",
)


def parse_env(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}

    if not path.exists():
        return output

    for raw_line in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]

        output[key] = value

    return output


def main() -> int:
    env = dict(os.environ)

    for path in ENV_FILES:
        for key, value in parse_env(path).items():
            env.setdefault(key, value)

    env["N8N_BLOCK_ENV_ACCESS_IN_NODE"] = "false"

    required = (
        "HUNTER_API_SECRET",
        "AADIL_APPS_SCRIPT_SECRET",
    )

    missing = [
        name
        for name in required
        if not str(env.get(name, "")).strip()
    ]

    if missing:
        print(
            "Missing required environment variable names: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    state = {
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": False,
        "HUNTER_API_SECRET_present": True,
        "AADIL_APPS_SCRIPT_SECRET_present": True,
        "AADIL_APPS_SCRIPT_URL_present": bool(
            str(env.get("AADIL_APPS_SCRIPT_URL", "")).strip()
        ),
        "secret_values_printed": False,
    }

    state_path = PROJECT / "logs" / "n8n_runtime_env_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )

    binary = "/usr/local/bin/n8n"

    if not Path(binary).exists():
        print(
            f"n8n binary not found: {binary}",
            file=sys.stderr,
        )
        return 3

    os.execve(binary, [binary, "start"], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
