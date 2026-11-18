"""Secure runtime credential resolution for user-facing integrations.

Environment variables remain authoritative for existing deployments. When an
environment value is absent, MUNSHI may read the corresponding secret from the
encrypted server-side vault. Secret values are never exposed by readiness APIs.
"""
from __future__ import annotations

import os
import re
from typing import Any

from app.database import get_setting
from app.secure_vault import read_secret_readonly, secret_metadata

TELEGRAM_BOT_TOKEN_TYPE = "telegram_bot_token"
TELEGRAM_CHAT_ID_TYPE = "telegram_chat_id"
SMTP2GO_API_KEY_TYPE = "smtp2go_api_key"
SMTP2GO_SENDER_TYPE = "smtp2go_sender"

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _vault_value(credential_type: str) -> str:
    try:
        return str(read_secret_readonly(credential_type) or "").strip()
    except Exception:
        return ""


def _configured(credential_type: str) -> bool:
    try:
        return secret_metadata(credential_type) is not None
    except (VaultError, Exception):
        return False


def resolve_credential(env_name: str, credential_type: str) -> tuple[str, str]:
    env_value = str(os.getenv(env_name) or "").strip()
    if env_value:
        return env_value, "environment"
    value = _vault_value(credential_type)
    if value:
        return value, "vault"
    return "", "missing"


def telegram_credentials() -> tuple[str, str]:
    token, _ = resolve_credential("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN_TYPE)
    chat_id, _ = resolve_credential("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID_TYPE)
    return token, chat_id


def telegram_configured() -> bool:
    token, chat_id = telegram_credentials()
    return bool(token and chat_id and chat_id.lstrip("-").isdigit())


def smtp2go_credentials() -> tuple[str, str]:
    api_key, _ = resolve_credential("MUNSHI_SMTP2GO_API_KEY", SMTP2GO_API_KEY_TYPE)
    sender, _ = resolve_credential("MUNSHI_SMTP2GO_SENDER", SMTP2GO_SENDER_TYPE)
    return api_key, sender


def smtp2go_configured() -> bool:
    api_key, sender = smtp2go_credentials()
    return bool(len(api_key) >= 16 and _EMAIL.fullmatch(sender or ""))


def integration_readiness() -> dict[str, Any]:
    telegram_token, telegram_token_source = resolve_credential(
        "TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN_TYPE
    )
    telegram_chat, telegram_chat_source = resolve_credential(
        "TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID_TYPE
    )
    smtp_key, smtp_key_source = resolve_credential(
        "MUNSHI_SMTP2GO_API_KEY", SMTP2GO_API_KEY_TYPE
    )
    smtp_sender, smtp_sender_source = resolve_credential(
        "MUNSHI_SMTP2GO_SENDER", SMTP2GO_SENDER_TYPE
    )
    mail_secret = str(os.getenv("MUNSHI_MAIL_SIGNING_SECRET") or "")
    external_mail_sync = (
        str(os.getenv("MUNSHI_MAIL_EXTERNAL_SYNC_CONFIRMED") or "")
        .strip()
        .casefold()
        in {"1", "true", "yes", "on"}
    )
    if not external_mail_sync:
        try:
            saved_sync = get_setting("mail_external_sync_confirmed_v1", {}) or {}
            external_mail_sync = bool(
                saved_sync.get("confirmed")
                if isinstance(saved_sync, dict)
                else saved_sync
            )
        except Exception:
            external_mail_sync = False
    return {
        "telegram": {
            "configured": bool(
                telegram_token
                and telegram_chat
                and telegram_chat.lstrip("-").isdigit()
            ),
            "token_source": telegram_token_source,
            "chat_source": telegram_chat_source,
            "vault_token_present": _configured(TELEGRAM_BOT_TOKEN_TYPE),
            "vault_chat_present": _configured(TELEGRAM_CHAT_ID_TYPE),
        },
        "smtp2go": {
            "configured": bool(
                len(smtp_key) >= 16 and _EMAIL.fullmatch(smtp_sender or "")
            ),
            "key_source": smtp_key_source,
            "sender_source": smtp_sender_source,
            "vault_key_present": _configured(SMTP2GO_API_KEY_TYPE),
            "vault_sender_present": _configured(SMTP2GO_SENDER_TYPE),
        },
        "mail_router": {
            "hunter_signing_secret_configured": len(mail_secret.encode()) >= 32,
            "external_sync_confirmed": external_mail_sync,
        },
    }
