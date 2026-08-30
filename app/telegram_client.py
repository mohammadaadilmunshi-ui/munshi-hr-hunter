from __future__ import annotations

import re

import hashlib
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from app.actions import get_company_rule_state
from app.database import ROOT_DIR, get_connection, get_setting
from app.telegram_control_center import format_job_added_at
from app.source_provenance import format_adapter_display

# AADIL_TELEGRAM_ADDED_AT_V1


load_dotenv(ROOT_DIR / ".env", override=True)

BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
).strip()

CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()

API_BASE = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
)


STATUS_LABELS = {
    "found": "FOUND",
    "held": "HELD",
    "approved_for_n8n": "APPROVED FOR N8N",
    "already_applied": "ALREADY APPLIED",
    "rejected_similar": "REJECTED SIMILAR",
    "blacklisted": "BLACKLISTED",
    "rejected": "REJECTED",
    "processing": "PROCESSING",
    "application_ready": "APPLICATION READY",
    "n8n_failed": "N8N FAILED",
    "retry_needed": "RETRY NEEDED",
}


class TelegramDeliveryAlreadyClaimed(RuntimeError):
    """Raised when a job already has a sent or uncertain delivery claim."""


def ensure_delivery_claims_schema(connection: Any | None = None) -> None:
    owns_connection = connection is None
    if connection is None:
        connection = get_connection()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_delivery_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                claim_token TEXT NOT NULL UNIQUE,
                delivery_state TEXT NOT NULL DEFAULT 'reserved',
                chat_id TEXT,
                message_id INTEGER,
                error_type TEXT,
                reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_telegram_delivery_claims_state
            ON telegram_delivery_claims(delivery_state, updated_at);
            """
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _eligibility_label() -> str:
    targeting = get_setting("targeting", {}) or {}
    eligibility = targeting.get("eligibility") if isinstance(targeting.get("eligibility"), dict) else {}
    mode = str(targeting.get("mode") or "Not configured")
    geography = str(eligibility.get("label") or "Not configured")
    return f"{mode} · {geography}"


def telegram_request(
    method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # AADIL_TELEGRAM_TRANSPORT_CALLBACK_RESILIENCE_V3
    from app.telegram_transport_v3 import telegram_api_request

    return telegram_api_request(
        method,
        payload,
        bot_token=BOT_TOKEN,
    )


def get_job(job_id: int) -> dict[str, Any]:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError(
            f"Job {job_id} was not found."
        )

    return dict(row)


def get_latest_action(job_id: int) -> str | None:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT event_type
            FROM events
            WHERE job_id = ?
              AND event_type LIKE 'job_action_%'
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    event_type = str(row["event_type"])

    return event_type.removeprefix(
        "job_action_"
    )


def readable_action(action: str | None) -> str | None:
    labels = {
        "hold": "Placed on hold",
        "restore": "Restored to found",
        "approve_for_n8n":
            "Approved locally for n8n",
        "already_applied":
            "Marked already applied",
        "reject_similar":
            "Rejected as similar",
        "boost_company":
            "Company boosted",
        "blacklist_company":
            "Company blacklisted",
    }

    if action is None:
        return None

    return labels.get(
        action,
        action.replace("_", " ").title(),
    )


def status_label(job: dict[str, Any]) -> str:
    status = str(
        job.get("status") or "found"
    )

    return STATUS_LABELS.get(
        status,
        status.replace("_", " ").upper(),
    )



def get_latest_n8n_result(
    job_id: int,
) -> dict[str, Any] | None:
    """Return the newest n8n result stored for a job."""

    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                id,
                job_id,
                n8n_status,
                final_ats_score,
                recruiter_found,
                outreach_draft_created,
                completed_at
            FROM n8n_results
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(job_id),),
        ).fetchone()

    except Exception:
        return None

    finally:
        connection.close()

    return dict(row) if row else None


def readable_boolean(value: Any) -> str:
    """Convert SQLite/API boolean values into card text."""

    if value is None:
        return "Not reported"

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"1", "true", "yes"}:
            return "Yes"

        if normalized in {"0", "false", "no", ""}:
            return "No"

    return "Yes" if bool(value) else "No"


def readable_utc_timestamp(value: Any) -> str:
    """Format an ISO timestamp consistently in UTC."""

    raw_value = str(value or "").strip()

    if not raw_value:
        return "Not reported"

    try:
        parsed = datetime.fromisoformat(
            raw_value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        parsed = parsed.astimezone(
            timezone.utc
        )

        return parsed.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except ValueError:
        return raw_value


def format_job_card(
    job: dict[str, Any],
    notice: str | None = None,
) -> str:
    score = int(
        round(job.get("hunter_score") or 0)
    )

    salary = (
        job.get("salary_raw")
        or "Not specified"
    )

    company_state = get_company_rule_state(
        str(job.get("company_name") or "")
    )

    company_state_label = {
        "boosted": "⭐ BOOSTED",
        "blacklisted": "⛔ BLACKLISTED",
        "normal": "Normal",
    }.get(
        company_state,
        company_state,
    )

    latest_action = readable_action(
        get_latest_action(int(job["id"]))
    )

    latest_n8n_result = get_latest_n8n_result(
        int(job["id"])
    )

    lines = [
        (
            f"<b>{html.escape(str(job['match_label']))}</b> "
            f"· Score <b>{score}</b>"
        ),
        "",
        f"<b>{html.escape(str(job['title']))}</b>",
        html.escape(str(job["company_name"])),
        "",
        (
            "📍 "
            + html.escape(
                str(
                    job.get("location_raw")
                    or "Not specified"
                )
            )
        ),
        (
            "🏢 "
            + html.escape(
                str(
                    job.get("remote_type")
                    or "Not specified"
                )
            )
        ),
        "💵 " + html.escape(str(salary)),
        "🕒 Added: " + html.escape(format_job_added_at(job)),
        # AADIL_JOB_ADAPTER_PROVENANCE_V1
        "🔌 Adapter: " + html.escape(format_adapter_display(job)),
        (
            "🎯 "
            + html.escape(
                str(
                    job.get("target_track")
                    or "General HR"
                )
            )
        ),
        "🛂 Eligibility: " + html.escape(_eligibility_label()),
        "",
        (
            "📌 Current decision: "
            f"<b>{html.escape(status_label(job))}</b>"
        ),
        (
            "🏷 Company rule: "
            f"<b>{html.escape(company_state_label)}</b>"
        ),
    ]

    if latest_action:
        lines.append(
            "🕘 Last action: "
            + html.escape(latest_action)
        )

    if latest_n8n_result:
        n8n_status = str(
            latest_n8n_result.get(
                "n8n_status",
                "unknown",
            )
        ).replace("_", " ").upper()

        ats_value = latest_n8n_result.get(
            "final_ats_score"
        )

        try:
            ats_display = (
                f"{float(ats_value):.0f}"
                if ats_value is not None
                else "Not reported"
            )
        except (TypeError, ValueError):
            ats_display = "Not reported"

        recruiter_display = readable_boolean(
            latest_n8n_result.get(
                "recruiter_found"
            )
        )

        outreach_display = readable_boolean(
            latest_n8n_result.get(
                "outreach_draft_created"
            )
        )

        completed_display = readable_utc_timestamp(
            latest_n8n_result.get(
                "completed_at"
            )
        )

        lines.extend(
            [
                "",
                "⚙️ <b>Automation result</b>",
                (
                    "✅ n8n: "
                    f"<b>{html.escape(n8n_status)}</b>"
                ),
                (
                    "📊 Final ATS: "
                    f"<b>{html.escape(ats_display)}</b>"
                ),
                (
                    "👤 Recruiter found: "
                    f"<b>{html.escape(recruiter_display)}</b>"
                ),
                (
                    "✉️ Outreach draft: "
                    f"<b>{html.escape(outreach_display)}</b>"
                ),
                (
                    "🕒 Completed: "
                    + html.escape(completed_display)
                ),
            ]
        )

    if notice:
        lines.extend(
            [
                "",
                "✅ " + html.escape(notice),
            ]
        )

    return "\n".join(lines)


def callback_button(
    *,
    text: str,
    job_id: int,
    action: str,
    active: bool = False,
    active_style: str = "success",
) -> dict[str, str]:
    button: dict[str, str] = {
        "text": text,
        "callback_data": (
            f"job:{job_id}:noop"
            if active
            else f"job:{job_id}:{action}"
        ),
    }

    if active:
        button["style"] = active_style

    return button


def build_keyboard(
    job: dict[str, Any],
) -> dict[str, Any]:
    job_id = int(job["id"])
    status = str(job.get("status") or "found")

    company_state = get_company_rule_state(
        str(job.get("company_name") or "")
    )

    def action_button(
        text: str,
        action: str,
        *,
        active: bool = False,
        style: str | None = None,
    ) -> dict[str, str]:
        button = {
            "text": text,
            "callback_data": (
                f"job:{job_id}:noop"
                if active
                else f"job:{job_id}:{action}"
            ),
        }

        if active and style:
            button["style"] = style

        return button

    approved = status == "approved_for_n8n"
    # AADIL_N8N_RUN_BUTTON_STATE_V1
    n8n_sent = str(dict(job).get("sent_to_n8n") or "0").strip().lower() not in {"", "0", "false", "none"}
    held = status == "held"
    applied = status == "already_applied"
    rejected = status == "rejected_similar"
    found = status == "found"

    boosted = company_state == "boosted"

    blacklisted = (
        company_state == "blacklisted"
        or status == "blacklisted"
    )

    rows = [
        [
            action_button(
                (
                    "✅ n8n COMPLETED"
                    if n8n_sent
                    else (
                        "▶️ Run n8n"
                        if approved
                        else "✅ Approve & Run n8n"
                    )
                ),
                "approve_for_n8n",
                active=n8n_sent,
                style="success",
            ),
            action_button(
                (
                    "⏸ CURRENT: HELD"
                    if held
                    else "⏸ Hold"
                ),
                "hold",
                active=held,
                style="primary",
            ),
        ],
        [
            action_button(
                (
                    "✔️ CURRENT: APPLIED"
                    if applied
                    else "✔️ Already applied"
                ),
                "already_applied",
                active=applied,
                style="success",
            ),
            action_button(
                (
                    "✅ CURRENT: FOUND"
                    if found
                    else "↩️ Restore"
                ),
                "restore",
                active=found,
                style="success",
            ),
        ],
        [
            action_button(
                (
                    "🚫 CURRENT: REJECTED"
                    if rejected
                    else "🚫 Reject similar"
                ),
                "reject_similar",
                active=rejected,
                style="danger",
            ),
        ],
        [
            action_button(
                (
                    "⭐ CURRENT: BOOSTED"
                    if boosted
                    else "⭐ Boost company"
                ),
                "boost_company",
                active=boosted,
                style="success",
            ),
            action_button(
                (
                    "⛔ CURRENT: BLACKLISTED"
                    if blacklisted
                    else "⛔ Blacklist company"
                ),
                "blacklist_company",
                active=blacklisted,
                style="danger",
            ),
        ],
    ]

    apply_url = str(
        job.get("apply_url") or ""
    ).strip()

    if apply_url.startswith(
        ("http://", "https://")
    ):
        rows.append(
            [
                {
                    "text": "🔗 Open job",
                    "url": apply_url,
                }
            ]
        )

    return {
        "inline_keyboard": rows,
    }


def send_job_card(
    job_id: int,
) -> int:
    if not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing."
        )

    job = get_job(job_id)
    request_payload = {
        "chat_id": CHAT_ID,
        "text": format_job_card(job),
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "reply_markup": json.dumps(build_keyboard(job)),
    }
    claim_token = uuid.uuid4().hex
    connection = get_connection()
    try:
        ensure_delivery_claims_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT telegram_sent FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if current is None:
            raise RuntimeError(f"Job {job_id} was not found.")
        existing = connection.execute(
            "SELECT delivery_state FROM telegram_delivery_claims WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if int(current["telegram_sent"] or 0) != 0 or existing is not None:
            state = str(existing["delivery_state"] if existing is not None else "sent")
            raise TelegramDeliveryAlreadyClaimed(
                f"Telegram delivery for job {job_id} is already claimed ({state})."
            )
        connection.execute(
            """
            INSERT INTO telegram_delivery_claims(job_id,claim_token,delivery_state,chat_id)
            VALUES (?,?,'reserved',?)
            """,
            (job_id, claim_token, str(CHAT_ID)),
        )
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES (?,'telegram_job_card_reserved','telegram','reserved',?)
            """,
            (job_id, json.dumps({"claim_token_sha256": hashlib.sha256(claim_token.encode()).hexdigest()})),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    try:
        response = telegram_request("sendMessage", request_payload)
    except Exception as error:
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE telegram_delivery_claims
                SET delivery_state='uncertain', error_type=?, updated_at=CURRENT_TIMESTAMP
                WHERE job_id=? AND claim_token=?
                """,
                (type(error).__name__, job_id, claim_token),
            )
            connection.execute(
                """
                INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
                VALUES (?,'telegram_job_card_delivery_uncertain','telegram','review_required',?)
                """,
                (job_id, json.dumps({"error_type": type(error).__name__})),
            )
            connection.commit()
        finally:
            connection.close()
        raise

    message_id = int(
        response["result"]["message_id"]
    )

    connection = get_connection()

    try:
        connection.execute(
            """
            UPDATE jobs
            SET
                telegram_sent = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )

        connection.execute(
            """
            UPDATE telegram_delivery_claims
            SET delivery_state='sent', message_id=?, sent_at=CURRENT_TIMESTAMP,
                error_type=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE job_id=? AND claim_token=?
            """,
            (message_id, job_id, claim_token),
        )

        connection.execute(
            """
            INSERT INTO events (
                job_id,
                event_type,
                actor,
                event_status,
                payload_json
            )
            VALUES (
                ?,
                'telegram_job_card_sent',
                'telegram',
                'completed',
                ?
            )
            """,
            (
                job_id,
                json.dumps(
                    {
                        "message_id": message_id,
                        "chat_id": CHAT_ID,
                    }
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()

    return message_id


def edit_job_card(
    chat_id: int,
    message_id: int,
    job_id: int,
    notice: str | None = None,
) -> bool:
    job = get_job(job_id)

    try:
        telegram_request(
            "editMessageText",
            {
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "text": format_job_card(
                    job,
                    notice=notice,
                ),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(
                    build_keyboard(job)
                ),
            },
        )

    except RuntimeError as error:
        if "message is not modified" in str(error):
            return False

        raise

    return True


def answer_callback(
    callback_query_id: str,
    text: str,
    show_alert: bool = False,
) -> None:
    telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_query_id,
            "text": text[:180],
            "show_alert": (
                "true"
                if show_alert
                else "false"
            ),
        },
    )

# AADIL_UNIFIED_ATS_TELEGRAM_RUNS_V1
_aadil_original_build_keyboard_unified_ats_v1 = build_keyboard
_aadil_original_telegram_request_unified_ats_v1 = telegram_request


def build_keyboard(job):
    markup = _aadil_original_build_keyboard_unified_ats_v1(job)
    try:
        from app.application_runs_v1 import decorate_job_keyboard
        job_id = int((job or {}).get("id") or (job or {}).get("job_id") or 0)
        if job_id > 0:
            return decorate_job_keyboard(markup, job_id)
    except Exception:
        pass
    return markup


def telegram_request(method, payload=None):
    prepared = payload
    if method == "sendMessage" and isinstance(payload, dict):
        try:
            from app.application_runs_v1 import decorate_completion_payload
            prepared = decorate_completion_payload(payload)
        except Exception:
            prepared = payload
    return _aadil_original_telegram_request_unified_ats_v1(method, prepared)

# AADIL_LIVE_TELEGRAM_PRESENTATION_V2
_aadil_v2_original_format_job_card = format_job_card
_aadil_v2_original_build_keyboard = build_keyboard
_aadil_v2_original_telegram_request = telegram_request


def format_job_card(job, *args, **kwargs):
    text = _aadil_v2_original_format_job_card(job, *args, **kwargs)
    try:
        from app.application_runs_v1 import adapter_names_for_job

        job_id = int((job or {}).get("id") or (job or {}).get("job_id") or 0)
        adapters = adapter_names_for_job(job_id) if job_id > 0 else []
        adapter_text = ", ".join(adapters) or str(
            (job or {}).get("source") or "Unknown"
        )
        line = f"🔌 Adapter(s): {adapter_text}"
        if re.search(r"(?m)^🔌 Adapter(?:\(s\))?:.*$", text):
            text = re.sub(
                r"(?m)^🔌 Adapter(?:\(s\))?:.*$",
                line,
                text,
                count=1,
            )
        else:
            text += "\n" + line
    except Exception:
        pass
    return text


def build_keyboard(job, *args, **kwargs):
    markup = _aadil_v2_original_build_keyboard(job, *args, **kwargs)
    try:
        from app.application_runs_v1 import decorate_job_keyboard

        job_id = int((job or {}).get("id") or (job or {}).get("job_id") or 0)
        if job_id > 0:
            markup = decorate_job_keyboard(markup, job_id)
    except Exception:
        pass
    return markup


def telegram_request(method, payload=None):
    prepared = payload
    if method == "sendMessage" and isinstance(payload, dict):
        try:
            from app.application_runs_v1 import decorate_telegram_payload

            prepared = decorate_telegram_payload(payload)
        except Exception:
            prepared = payload
    return _aadil_v2_original_telegram_request(method, prepared)

# AADIL_FORCE_RERUN_NORMAL_JOB_CARD_V1_1
_aadil_build_keyboard_before_force_rerun_v1_1 = build_keyboard


def build_keyboard(job: dict[str, Any]) -> dict[str, Any]:
    """Add a job-specific Force Rerun button to normal stored-job cards."""
    markup = dict(
        _aadil_build_keyboard_before_force_rerun_v1_1(job)
        or {}
    )
    rows = [
        list(row)
        for row in (
            markup.get("inline_keyboard")
            or []
        )
    ]
    job_id = int(job["id"])
    callback = f"app:force:{job_id}"
    existing_callbacks = {
        str(button.get("callback_data") or "")
        for row in rows
        for button in row
        if isinstance(button, dict)
    }

    if callback not in existing_callbacks:
        try:
            from app.force_rerun_v1 import next_rerun_part
            part = int(next_rerun_part(job_id))
        except Exception:
            part = 2

        rows.insert(
            1 if rows else 0,
            [{
                "text": f"🔁 Force Rerun as Part {part}",
                "callback_data": callback,
            }],
        )

    markup["inline_keyboard"] = rows
    return markup
# AADIL_TELEGRAM_SCORECARDS_V1 — BEGIN
_aadil_scorecards_original_format_job_card_v1 = format_job_card
_aadil_scorecards_original_build_keyboard_v1 = build_keyboard

def format_job_card(job, *args, **kwargs):
    text = _aadil_scorecards_original_format_job_card_v1(
        job,
        *args,
        **kwargs,
    )
    if "🧠 <b>Why this score</b>" not in text:
        try:
            from app.telegram_scorecards_v1 import (
                compact_score_reason_lines,
            )
            extra_lines = compact_score_reason_lines(dict(job or {}))
            if extra_lines:
                candidate = (
                    text.rstrip()
                    + "\n\n"
                    + "\n".join(extra_lines)
                )
                if len(candidate) <= 4000:
                    text = candidate
                else:
                    minimal = (
                        text.rstrip()
                        + "\n\n🧠 <b>Why this score</b>\n"
                        + "Tap “Why this Hunter score?” for the stored breakdown."
                    )
                    if len(minimal) <= 4000:
                        text = minimal
        except Exception:
            pass
    return text


def build_keyboard(job, *args, **kwargs):
    keyboard = _aadil_scorecards_original_build_keyboard_v1(
        job,
        *args,
        **kwargs,
    )
    try:
        job_data = dict(job or {})
    except (TypeError, ValueError):
        job_data = job if isinstance(job, dict) else {}

    try:
        job_id = int(
            job_data.get("id")
            or job_data.get("job_id")
            or 0
        )
    except (TypeError, ValueError):
        job_id = 0

    if job_id <= 0 or not isinstance(keyboard, dict):
        return keyboard

    rows = keyboard.get("inline_keyboard")
    if not isinstance(rows, list):
        return keyboard

    callback_data = f"cc:sc:why:{job_id}"
    exists = any(
        isinstance(button, dict)
        and button.get("callback_data") == callback_data
        for row in rows
        if isinstance(row, list)
        for button in row
    )
    if not exists:
        rows.append(
            [
                {
                    "text": "🧠 Why this Hunter score?",
                    "callback_data": callback_data,
                }
            ]
        )
    return keyboard
# AADIL_TELEGRAM_SCORECARDS_V1 — END
