from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

HR_AGENT_DIR = (
    Path.home()
    / "Documents"
    / "AI-Tools"
    / "hiring-agent"
)

HR_AGENT_PYTHON = (
    HR_AGENT_DIR
    / ".venv"
    / "bin"
    / "python"
)

HR_AGENT_SCRIPT = (
    HR_AGENT_DIR
    / "n8n_hr_score.py"
)

router = APIRouter()


class HRAgentRequest(BaseModel):
    hr_agent_payload_b64: str = Field(
        min_length=4,
        max_length=5_000_000,
    )


def load_secret() -> str:
    environment_value = str(
        os.getenv("HUNTER_API_SECRET") or ""
    ).strip()

    if environment_value:
        return environment_value

    if not ENV_PATH.exists():
        return ""

    for raw_line in ENV_PATH.read_text().splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)

        if key.strip() == "HUNTER_API_SECRET":
            return value.strip().strip("\"'")

    return ""


def validate_secret(
    supplied_secret: str | None,
) -> None:
    expected_secret = load_secret()

    if not expected_secret:
        raise HTTPException(
            status_code=503,
            detail="HUNTER_API_SECRET is missing.",
        )

    if supplied_secret != expected_secret:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )


def decode_payload(
    encoded_payload: str,
) -> str:
    try:
        decoded_text = base64.b64decode(
            encoded_payload,
            validate=True,
        ).decode("utf-8")

        parsed_payload = json.loads(
            decoded_text
        )

    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid HR Agent payload.",
        ) from error

    if not isinstance(
        parsed_payload,
        dict,
    ):
        raise HTTPException(
            status_code=400,
            detail="Payload must be a JSON object.",
        )

    return decoded_text


@router.post("/api/hr-agent/score")
def score_resume(
    request: HRAgentRequest,
    x_hunter_secret: str | None = Header(
        default=None,
        alias="X-Hunter-Secret",
    ),
) -> dict[str, object]:
    validate_secret(
        x_hunter_secret
    )

    decoded_payload = decode_payload(
        request.hr_agent_payload_b64
    )

    if not HR_AGENT_PYTHON.is_file():
        raise HTTPException(
            status_code=503,
            detail="HR Agent Python is unavailable.",
        )

    if not HR_AGENT_SCRIPT.is_file():
        raise HTTPException(
            status_code=503,
            detail="HR Agent script is unavailable.",
        )

    try:
        result = subprocess.run(
            [
                str(HR_AGENT_PYTHON),
                str(HR_AGENT_SCRIPT),
            ],
            cwd=HR_AGENT_DIR,
            input=decoded_payload,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )

        return {
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "exitCode": int(result.returncode),
            "proxy_status": "completed",
        }

    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "HR Agent timed out.",
            "exitCode": 124,
            "proxy_status": "timeout",
        }

    except Exception as error:
        return {
            "stdout": "",
            "stderr": str(error),
            "exitCode": 1,
            "proxy_status": "failed",
        }
