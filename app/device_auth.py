"""Persistent trusted-device authentication for the MUNSHI dashboard edge.

Caddy keeps the existing password check only on the enrollment endpoint. After
successful enrollment this module issues a host-only, HttpOnly, Secure signed
cookie. Normal dashboard and Streamlit websocket requests are then checked by
Caddy ``forward_auth`` without re-running HTTP Basic Auth.

This module grants dashboard access only. It does not grant API, n8n, ATS,
submission, email, or application authority.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.database import DB_PATH


router = APIRouter(include_in_schema=False)

_AUDIENCE = "munshi-dashboard-device-auth-v1"
_DEFAULT_COOKIE_NAME = "__Host-munshi_device_session"
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
_MIN_TTL_SECONDS = 60 * 60
_MAX_TTL_SECONDS = 90 * 24 * 60 * 60
_KEY_BYTES = 32
_KEY_CACHE: tuple[Path, bytes] | None = None


def cookie_name() -> str:
    configured = os.getenv("MUNSHI_DEVICE_AUTH_COOKIE_NAME", "").strip()
    return configured or _DEFAULT_COOKIE_NAME


def session_ttl_seconds() -> int:
    raw = os.getenv("MUNSHI_DEVICE_AUTH_TTL_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("MUNSHI_DEVICE_AUTH_TTL_SECONDS must be an integer") from error
    return max(_MIN_TTL_SECONDS, min(value, _MAX_TTL_SECONDS))


def signing_key_path() -> Path:
    configured = os.getenv("MUNSHI_DEVICE_AUTH_KEY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(DB_PATH).resolve().parent / ".dashboard_device_auth_key").resolve()


def reset_signing_key_cache() -> None:
    """Test/support hook; it never deletes persistent key material."""
    global _KEY_CACHE
    _KEY_CACHE = None


def _load_signing_key() -> bytes:
    global _KEY_CACHE
    path = signing_key_path()
    if _KEY_CACHE is not None and _KEY_CACHE[0] == path:
        return _KEY_CACHE[1]

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        candidate = secrets.token_bytes(_KEY_BYTES)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            key = path.read_bytes()
        else:
            try:
                os.write(fd, candidate)
                os.fsync(fd)
            finally:
                os.close(fd)
            key = candidate

    if len(key) < _KEY_BYTES:
        raise RuntimeError("MUNSHI dashboard device-auth signing key is invalid")
    try:
        path.chmod(0o600)
    except OSError:
        # Existing container/volume ownership remains authoritative. Failure to
        # chmod must not replace or expose the key; normal reads still fail
        # closed if permissions are actually unusable.
        pass

    _KEY_CACHE = (path, key)
    return key


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _normalized_user(value: str | None) -> str:
    user = str(value or "").strip()
    if not user or len(user) > 256 or "\r" in user or "\n" in user:
        return ""
    return user


def mint_device_token(
    user: str,
    *,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> str:
    subject = _normalized_user(user)
    if not subject:
        raise ValueError("dashboard auth user is required")
    issued_at = int(time.time() if now is None else now)
    ttl = session_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    ttl = max(_MIN_TTL_SECONDS, min(ttl, _MAX_TTL_SECONDS))
    payload = {
        "aud": _AUDIENCE,
        "exp": issued_at + ttl,
        "iat": issued_at,
        "nonce": secrets.token_urlsafe(16),
        "sub": subject,
        "v": 1,
    }
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    encoded = _b64url_encode(payload_bytes)
    signature = hmac.new(
        _load_signing_key(),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded}.{_b64url_encode(signature)}"


def verify_device_token(token: str | None, *, now: int | None = None) -> dict[str, Any] | None:
    raw = str(token or "").strip()
    if not raw or raw.count(".") != 1:
        return None
    encoded, signature_text = raw.split(".", 1)
    try:
        supplied_signature = _b64url_decode(signature_text)
        expected_signature = hmac.new(
            _load_signing_key(),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("aud") != _AUDIENCE or payload.get("v") != 1:
        return None
    subject = _normalized_user(payload.get("sub"))
    if not subject:
        return None
    try:
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    current = int(time.time() if now is None else now)
    if issued_at > current + 300 or expires_at <= current or expires_at <= issued_at:
        return None
    return payload


def safe_next_path(value: str | None) -> str:
    target = str(value or "/").strip()
    if (
        not target.startswith("/")
        or target.startswith("//")
        or "\r" in target
        or "\n" in target
        or len(target) > 4096
    ):
        return "/"
    if target.startswith("/_munshi-auth/"):
        return "/"
    return target


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/_munshi-auth/login")
def dashboard_device_login(
    next_path: str = Query(default="/", alias="next", max_length=4096),
    x_munshi_auth_user: str | None = Header(default=None, alias="X-Munshi-Auth-User"),
) -> Response:
    """Enroll this browser after Caddy has successfully checked the password."""
    user = _normalized_user(x_munshi_auth_user)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard password enrollment was not authenticated by the edge.",
        )

    ttl = session_ttl_seconds()
    token = mint_device_token(user, ttl_seconds=ttl)
    response = RedirectResponse(url=safe_next_path(next_path), status_code=303)
    response.set_cookie(
        key=cookie_name(),
        value=token,
        max_age=ttl,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return _no_store(response)


@router.get("/_munshi-auth/verify")
def dashboard_device_verify(
    request: Request,
    x_munshi_original_uri: str | None = Header(default=None, alias="X-Munshi-Original-URI"),
) -> Response:
    """Caddy forward-auth endpoint for normal HTTP and Streamlit reconnects."""
    payload = verify_device_token(request.cookies.get(cookie_name()))
    if payload is not None:
        response = Response(status_code=204)
        response.headers["X-Munshi-Auth-User"] = str(payload["sub"])
        return _no_store(response)

    target = safe_next_path(x_munshi_original_uri)
    login_url = "/_munshi-auth/login?next=" + quote(target, safe="")
    response = RedirectResponse(url=login_url, status_code=303)
    return _no_store(response)


@router.api_route("/_munshi-auth/logout", methods=["GET", "POST"])
def dashboard_device_logout() -> Response:
    response = RedirectResponse(url="/_munshi-auth/login?next=%2F", status_code=303)
    response.delete_cookie(
        key=cookie_name(),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return _no_store(response)
