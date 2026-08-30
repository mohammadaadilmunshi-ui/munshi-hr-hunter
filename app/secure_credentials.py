from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SERVICE = "com.aadil.hr-hunter.usajobs"
API_ACCOUNT = "USAJOBS_API_KEY"
EMAIL_ACCOUNT = "USAJOBS_EMAIL"
MARKER = "AADIL_USAJOBS_KEYCHAIN_V19"


class CredentialError(RuntimeError):
    pass


def _security_path() -> str:
    path = shutil.which("security") or "/usr/bin/security"
    if not Path(path).exists():
        raise CredentialError("macOS Keychain utility /usr/bin/security is unavailable.")
    return path


def _security(args: list[str]) -> subprocess.CompletedProcess[str]:
    # Never print or log arguments. Some calls contain a secret.
    return subprocess.run(
        [_security_path(), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def _get_keychain(account: str) -> str:
    result = _security(
        ["find-generic-password", "-a", account, "-s", SERVICE, "-w"]
    )
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def _set_keychain(account: str, value: str) -> None:
    value = str(value or "").strip()
    if not value:
        raise CredentialError(f"Refusing to store an empty value for {account}.")
    result = _security(
        [
            "add-generic-password",
            "-a",
            account,
            "-s",
            SERVICE,
            "-U",
            "-w",
            value,
        ]
    )
    if result.returncode != 0:
        detail = str(result.stderr or "").strip() or "unknown Keychain error"
        raise CredentialError(f"macOS Keychain rejected {account}: {detail}")


def _delete_keychain(account: str) -> None:
    _security(["delete-generic-password", "-a", account, "-s", SERVICE])


def _mask(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    suffix = value[-4:] if len(value) >= 4 else ""
    return f"••••••••{suffix}" if suffix else "••••"


def get_usajobs_credentials() -> tuple[str, str]:
    env_key = str(os.getenv("USAJOBS_API_KEY") or "").strip()
    env_email = str(os.getenv("USAJOBS_EMAIL") or "").strip()
    key = env_key or _get_keychain(API_ACCOUNT)
    email = env_email or _get_keychain(EMAIL_ACCOUNT)
    return key, email


def credential_status() -> dict[str, Any]:
    env_key = str(os.getenv("USAJOBS_API_KEY") or "").strip()
    env_email = str(os.getenv("USAJOBS_EMAIL") or "").strip()
    kc_key = "" if env_key else _get_keychain(API_ACCOUNT)
    kc_email = "" if env_email else _get_keychain(EMAIL_ACCOUNT)
    key = env_key or kc_key
    email = env_email or kc_email
    return {
        "api_key_present": bool(key),
        "api_key_masked": _mask(key),
        "email_present": bool(email),
        "email": email,
        "api_key_source": (
            "Environment" if env_key else ("macOS Keychain" if kc_key else "Not configured")
        ),
        "email_source": (
            "Environment" if env_email else ("macOS Keychain" if kc_email else "Not configured")
        ),
        "keychain_available": Path(shutil.which("security") or "/usr/bin/security").exists(),
    }


def save_credentials(*, api_key: str | None, email: str) -> dict[str, Any]:
    api_key = str(api_key or "").strip()
    email = str(email or "").strip()
    existing_key, _ = get_usajobs_credentials()

    if not email or "@" not in email:
        raise CredentialError(
            "Enter the same valid email address used for the USAJOBS API request."
        )
    if not api_key and not existing_key:
        raise CredentialError("Enter the USAJOBS API key before saving.")

    if os.getenv("USAJOBS_API_KEY"):
        if api_key:
            raise CredentialError(
                "USAJOBS_API_KEY currently comes from the process environment. "
                "Remove that override before replacing it from the dashboard."
            )
    elif api_key:
        _set_keychain(API_ACCOUNT, api_key)

    if os.getenv("USAJOBS_EMAIL"):
        current = str(os.getenv("USAJOBS_EMAIL") or "").strip()
        if email != current:
            raise CredentialError(
                "USAJOBS_EMAIL currently comes from the process environment. "
                "Remove that override before replacing it from the dashboard."
            )
    else:
        _set_keychain(EMAIL_ACCOUNT, email)

    _set_health(
        "needs_credentials",
        "USAJobs credentials stored securely; run credential verification before enabling.",
    )
    return credential_status()


def delete_credentials() -> dict[str, Any]:
    if os.getenv("USAJOBS_API_KEY") or os.getenv("USAJOBS_EMAIL"):
        raise CredentialError(
            "USAJobs credentials currently come from the process environment. "
            "Remove those environment variables before deleting dashboard-managed credentials."
        )
    _delete_keychain(API_ACCOUNT)
    _delete_keychain(EMAIL_ACCOUNT)
    _set_health(
        "needs_credentials",
        "Set USAJOBS_API_KEY and USAJOBS_EMAIL before enabling.",
    )
    return credential_status()


def test_credentials() -> dict[str, Any]:
    key, email = get_usajobs_credentials()
    if not key or not email:
        return {
            "ok": False,
            "http_status": None,
            "message": "Both the USAJobs API key and API-request email are required.",
        }

    query = urllib.parse.urlencode(
        {"Keyword": "Human Resources", "ResultsPerPage": 1}
    )
    request = urllib.request.Request(
        "https://data.usajobs.gov/api/search?" + query,
        method="GET",
        headers={
            "Host": "data.usajobs.gov",
            "User-Agent": email,
            "Authorization-Key": key,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read(1_500_000).decode("utf-8", errors="replace")
        payload = json.loads(raw)
        result = payload.get("SearchResult") if isinstance(payload, dict) else {}
        returned = int((result or {}).get("SearchResultCount") or 0)
        total = int((result or {}).get("SearchResultCountAll") or 0)

        if 200 <= status < 300:
            _set_health("installed_disabled", None)
            return {
                "ok": True,
                "http_status": status,
                "returned": returned,
                "total_matches": total,
                "message": "USAJOBS credentials verified successfully.",
            }
        return {
            "ok": False,
            "http_status": status,
            "message": f"USAJOBS returned HTTP {status}.",
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "http_status": int(exc.code),
            "message": f"USAJOBS rejected the request with HTTP {exc.code}.",
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "http_status": None,
            "message": f"Network error while contacting USAJOBS: {exc.reason}",
        }
    except json.JSONDecodeError:
        return {
            "ok": False,
            "http_status": None,
            "message": "USAJOBS returned a response that was not valid JSON.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "http_status": None,
            "message": f"{type(exc).__name__}: {exc}",
        }


def _set_health(health_status: str, last_error: str | None) -> None:
    # Credential actions may update USAJobs telemetry, but never enable the source.
    try:
        from app.database import get_connection

        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE source_health
                SET health_status=?,
                    last_error=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source_name='USAJobs' AND enabled=0
                """,
                (health_status, last_error),
            )
            connection.commit()
        finally:
            connection.close()
    except Exception:
        return
