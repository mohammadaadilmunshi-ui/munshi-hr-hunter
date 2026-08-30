from __future__ import annotations

import asyncio
import html
import json
import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.database import get_connection

FEATURE_MARKER = "AADIL_TELEGRAM_SCORECARDS_V1"
WINDOW_HOURS = 24
PAGE_SIZE = 5
EASTERN = ZoneInfo("America/New_York")

BUCKETS: tuple[dict[str, Any], ...] = (
    {"key": "u50", "label": "Under 50", "minimum": None, "maximum": 50.0, "emoji": "🔻"},
    {"key": "50_59", "label": "50–59", "minimum": 50.0, "maximum": 60.0, "emoji": "🟠"},
    {"key": "60_69", "label": "60–69", "minimum": 60.0, "maximum": 70.0, "emoji": "🟡"},
    {"key": "70_79", "label": "70–79", "minimum": 70.0, "maximum": 80.0, "emoji": "🔵"},
    {"key": "80_89", "label": "80–89", "minimum": 80.0, "maximum": 90.0, "emoji": "🟢"},
    {"key": "90_94", "label": "90–94", "minimum": 90.0, "maximum": 95.0, "emoji": "⭐"},
    {"key": "95p", "label": "95+", "minimum": 95.0, "maximum": None, "emoji": "🏆"},
)

BUCKET_BY_KEY = {str(item["key"]): item for item in BUCKETS}

ROLE_REASONS = {
    "exact_target_role": "exact dashboard target-role match",
    "partial_target_role": "partial dashboard target-role match",
    "target_track_match": "matched a configured HR target track",
    "no_target_role_match": "no configured target-role match",
}

SALARY_REASONS = {
    "meets_minimum": "meets the configured minimum compensation",
    "partially_meets_minimum": "maximum compensation meets the configured minimum",
    "below_minimum": "below the configured minimum compensation",
    "unknown_allowed": "compensation was not listed; unknown salary is allowed",
    "unknown_penalty": "compensation was not listed and received a penalty",
    "unpaid": "role appears unpaid",
}

AUTHORIZATION_REASONS = {
    "explicit_cpt_or_opt": "posting explicitly mentions CPT or OPT",
    "generic_work_authorization": "posting contains a generic work-authorization statement",
    "authorization_not_specified": "posting does not specify work authorization",
}


def _bucket(key: str) -> dict[str, Any]:
    bucket = BUCKET_BY_KEY.get(str(key))
    if bucket is None:
        raise ValueError(f"Unknown score bucket: {key}")
    return bucket


def _score_where(bucket: dict[str, Any], alias: str = "j") -> tuple[str, list[float]]:
    minimum = bucket.get("minimum")
    maximum = bucket.get("maximum")
    column = f"COALESCE({alias}.hunter_score, 0)"

    if minimum is None and maximum is not None:
        return f"{column} < ?", [float(maximum)]
    if minimum is not None and maximum is None:
        return f"{column} >= ?", [float(minimum)]
    if minimum is not None and maximum is not None:
        return f"{column} >= ? AND {column} < ?", [float(minimum), float(maximum)]
    return "1 = 1", []


def _sent_cte() -> str:
    # send_job_card() writes telegram_job_card_sent. The automatic dispatcher
    # may also write telegram_job_auto_delivered, so GROUP BY removes the pair.
    return """
        WITH delivered AS (
            SELECT
                job_id,
                MAX(created_at) AS delivered_at
            FROM events
            WHERE job_id IS NOT NULL
              AND event_type IN (
                  'telegram_job_card_sent',
                  'telegram_job_auto_delivered'
              )
              AND datetime(created_at) >= datetime('now', '-24 hours')
            GROUP BY job_id
        )
    """


def load_24h_counts() -> dict[str, int]:
    result = {str(bucket["key"]): 0 for bucket in BUCKETS}
    connection = get_connection()
    try:
        rows = connection.execute(
            _sent_cte()
            + """
            SELECT j.hunter_score
            FROM delivered d
            JOIN jobs j ON j.id = d.job_id
            """
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        try:
            score = float(row["hunter_score"] or 0)
        except (TypeError, ValueError):
            score = 0.0

        for bucket in BUCKETS:
            minimum = bucket.get("minimum")
            maximum = bucket.get("maximum")
            if minimum is not None and score < float(minimum):
                continue
            if maximum is not None and score >= float(maximum):
                continue
            result[str(bucket["key"])] += 1
            break

    return result


def load_bucket_job_ids(
    bucket_key: str,
    offset: int = 0,
    page_size: int = PAGE_SIZE,
) -> tuple[list[int], int]:
    bucket = _bucket(bucket_key)
    where_sql, parameters = _score_where(bucket)
    offset = max(0, int(offset or 0))
    page_size = max(1, min(int(page_size or PAGE_SIZE), 10))

    connection = get_connection()
    try:
        total = int(
            connection.execute(
                _sent_cte()
                + f"""
                SELECT COUNT(*)
                FROM delivered d
                JOIN jobs j ON j.id = d.job_id
                WHERE {where_sql}
                """,
                parameters,
            ).fetchone()[0]
        )

        rows = connection.execute(
            _sent_cte()
            + f"""
            SELECT j.id
            FROM delivered d
            JOIN jobs j ON j.id = d.job_id
            WHERE {where_sql}
            ORDER BY
                COALESCE(j.hunter_score, 0) DESC,
                datetime(d.delivered_at) DESC,
                j.id DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, offset],
        ).fetchall()
    finally:
        connection.close()

    return [int(row["id"]) for row in rows], total


def load_job(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row is not None else None


def _load_breakdown(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("score_breakdown_json")
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _signed(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        rendered = str(int(number))
    else:
        rendered = f"{number:.1f}"
    return f"+{rendered}" if number > 0 else rendered


def _clean_reason(value: Any, mapping: dict[str, str] | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if mapping and text in mapping:
        return mapping[text]
    return text.replace("_", " ")


def _component_rows(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    specifications = (
        ("role_score", "Role match", "role_reason", ROLE_REASONS),
        ("location_score", "Location", "location_match", None),
        ("skills_score", "Boosted skills", None, None),
        ("salary_score", "Compensation", "salary_reason", SALARY_REASONS),
        ("authorization_score", "Work authorization", "authorization_reason", AUTHORIZATION_REASONS),
        ("freshness_score", "Posting freshness", "age_days", None),
        ("company_score", "Company watchlist", None, None),
    )

    rows: list[dict[str, Any]] = []
    used: set[str] = set()

    for score_key, label, reason_key, mapping in specifications:
        value = _number(breakdown.get(score_key))
        if value is None:
            continue
        used.add(score_key)
        reason = ""
        if reason_key == "age_days":
            age = breakdown.get("age_days")
            reason = f"{age} day(s) old" if age not in (None, "") else ""
        elif reason_key:
            reason = _clean_reason(breakdown.get(reason_key), mapping)
        rows.append({"key": score_key, "label": label, "score": value, "reason": reason})

    ignored = {
        "final_score",
        "raw_score",
        "ghost_risk_score",
        "total_score",
        "hunter_score",
    }
    for key, raw_value in breakdown.items():
        if key in used or key in ignored or not str(key).endswith("_score"):
            continue
        value = _number(raw_value)
        if value is None:
            continue
        label = str(key)[:-6].replace("_", " ").strip().title()
        rows.append({"key": str(key), "label": label, "score": value, "reason": ""})

    return rows


def compact_score_reason_lines(job: dict[str, Any]) -> list[str]:
    breakdown = _load_breakdown(job)
    if not breakdown:
        return [
            "🧠 <b>Why this score</b>",
            "Structured scoring evidence is unavailable for this older record.",
        ]

    rows = _component_rows(breakdown)
    positives = sorted(
        (row for row in rows if float(row["score"]) > 0),
        key=lambda row: float(row["score"]),
        reverse=True,
    )
    penalties = sorted(
        (row for row in rows if float(row["score"]) < 0),
        key=lambda row: float(row["score"]),
    )

    lines = ["🧠 <b>Why this score</b>"]

    if positives:
        lines.append(
            "✅ Strongest gains: "
            + ", ".join(
                f"{html.escape(str(row['label']))} {_signed(row['score'])}"
                for row in positives[:3]
            )
        )

    if penalties:
        lines.append(
            "⚠️ Main deductions: "
            + ", ".join(
                f"{html.escape(str(row['label']))} {_signed(row['score'])}"
                for row in penalties[:2]
            )
        )

    keywords = breakdown.get("matched_boosted_keywords")
    if isinstance(keywords, list) and keywords:
        lines.append(
            "🔑 Matched boosts: "
            + html.escape(", ".join(str(item) for item in keywords[:5]))
        )

    if len(lines) == 1:
        lines.append("Tap the explanation button for the stored component details.")

    lines.append("🔎 Full component explanation is available below.")
    return lines[:5]


def format_score_explanation(job: dict[str, Any]) -> str:
    breakdown = _load_breakdown(job)
    score = _number(job.get("hunter_score")) or 0.0
    title = html.escape(str(job.get("title") or "Unknown role"))
    company = html.escape(str(job.get("company_name") or "Unknown company"))

    lines = [
        "🧠 <b>Hunter Score Explanation</b>",
        "",
        f"<b>{title}</b>",
        company,
        f"🎯 Final Hunter score: <b>{score:.0f}/100</b>",
    ]

    hard_rejection = str(
        job.get("hard_rejection_reason")
        or breakdown.get("hard_rejection_reason")
        or ""
    ).strip()
    if hard_rejection:
        lines.extend(
            [
                "",
                "⛔ <b>Hard-rejection evidence</b>",
                html.escape(hard_rejection),
            ]
        )

    if not breakdown:
        lines.extend(
            [
                "",
                "This record does not contain structured scoring evidence. "
                "No explanation was invented.",
            ]
        )
        return "\n".join(lines)

    rows = _component_rows(breakdown)
    if rows:
        lines.extend(["", "📐 <b>Scoring components</b>"])
        for row in rows:
            icon = "✅" if row["score"] > 0 else ("⚠️" if row["score"] < 0 else "➖")
            detail = f" — {html.escape(str(row['reason']))}" if row.get("reason") else ""
            lines.append(
                f"{icon} {html.escape(str(row['label']))}: "
                f"<b>{_signed(row['score'])}</b>{detail}"
            )

    keywords = breakdown.get("matched_boosted_keywords")
    if isinstance(keywords, list) and keywords:
        lines.extend(
            [
                "",
                "🔑 <b>Matched boosted keywords</b>",
                html.escape(", ".join(str(item) for item in keywords[:12])),
            ]
        )

    rejected = breakdown.get("matched_rejected_keywords")
    if isinstance(rejected, list) and rejected:
        lines.extend(
            [
                "",
                "🚫 <b>Matched rejected or penalty keywords</b>",
                html.escape(", ".join(str(item) for item in rejected[:12])),
            ]
        )

    target_track = (
        breakdown.get("target_track")
        or job.get("target_track")
    )
    if target_track:
        lines.extend(
            [
                "",
                "🎯 <b>Target track</b>",
                html.escape(str(target_track)),
            ]
        )

    track_matches = breakdown.get("track_matches")
    if isinstance(track_matches, dict) and track_matches:
        ranked = sorted(
            (
                (str(key), _number(value) or 0)
                for key, value in track_matches.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        rendered = ", ".join(
            f"{name}: {value:g}"
            for name, value in ranked[:6]
            if value
        )
        if rendered:
            lines.append("Track evidence: " + html.escape(rendered))

    raw_score = _number(breakdown.get("raw_score"))
    final_score = _number(breakdown.get("final_score"))
    if raw_score is not None or final_score is not None:
        lines.extend(["", "🧮 <b>Calculation</b>"])
        if raw_score is not None:
            lines.append(f"Raw component total: <b>{raw_score:g}</b>")
        if final_score is not None:
            lines.append(f"Stored final score: <b>{final_score:g}</b>")
        if raw_score is not None and final_score is not None and raw_score != final_score:
            lines.append(
                "The final value differs because the scorer applies caps, "
                "floors, or a rejection/quality gate."
            )

    ghost_risk = _number(job.get("ghost_risk_score"))
    if ghost_risk and ghost_risk > 0:
        lines.extend(
            [
                "",
                f"👻 Ghost/staleness risk: <b>{ghost_risk:g}</b>",
            ]
        )

    if job.get("cpt_trapdoor"):
        lines.extend(
            [
                "",
                "🛂 CPT review flag: <b>Yes</b>",
            ]
        )

    scoring_version = job.get("scoring_version")
    last_scored_at = job.get("last_scored_at")
    if scoring_version or last_scored_at:
        lines.extend(["", "ℹ️ <b>Scoring record</b>"])
        if scoring_version:
            lines.append("Version: " + html.escape(str(scoring_version)))
        if last_scored_at:
            lines.append("Last scored: " + html.escape(str(last_scored_at)) + " UTC")

    return "\n".join(lines)[:3900]


def _telegram_request(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    from app.telegram_client import telegram_request
    response = telegram_request(method, payload)
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError(
            f"Telegram rejected {method}: "
            + json.dumps(response, ensure_ascii=False, default=str)
        )
    return response


def _send_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> int:
    payload: dict[str, Any] = {
        "chat_id": str(int(chat_id)),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

    response = _telegram_request("sendMessage", payload)
    return int((response.get("result") or {}).get("message_id") or 0)


def scorecard_menu_keyboard(counts: dict[str, int]) -> dict[str, Any]:
    buttons = []
    for bucket in BUCKETS:
        count = int(counts.get(str(bucket["key"]), 0))
        buttons.append(
            {
                "text": f"{bucket['emoji']} {bucket['label']} ({count})",
                "callback_data": f"cc:sc:b:{bucket['key']}:0",
            }
        )

    rows = [
        buttons[0:2],
        buttons[2:4],
        buttons[4:6],
        buttons[6:7],
        [
            {"text": "🔄 Refresh counts", "callback_data": "cc:sc:menu"},
            {"text": "✖️ Close", "callback_data": "cc:sc:close"},
        ],
    ]
    return {"inline_keyboard": rows}


def send_scorecard_menu(chat_id: int) -> dict[str, Any]:
    counts = load_24h_counts()
    total = sum(counts.values())
    now = datetime.now(EASTERN)
    clock = now.strftime("%I:%M %p").lstrip("0")
    now_local = f"{now:%b} {now.day}, {now.year} · {clock} ET"

    message_id = _send_message(
        chat_id,
        (
            "📊 <b>Telegram Job Cards — Past 24 Hours</b>\n\n"
            "Only scraped job cards actually delivered to Telegram are counted. "
            "Reopened preview cards are excluded.\n\n"
            f"Total delivered jobs: <b>{total}</b>\n"
            f"Updated: {html.escape(now_local)}\n\n"
            "Choose a Hunter-score range:"
        ),
        reply_markup=scorecard_menu_keyboard(counts),
    )

    return {
        "success": True,
        "message": f"24-hour scorecard menu opened with {total} delivered job(s).",
        "message_id": message_id,
        "counts": counts,
        "total": total,
    }


def _send_history_job_card(job_id: int, chat_id: int) -> int:
    from app.telegram_client import build_keyboard, format_job_card, get_job

    job = get_job(int(job_id))
    if not job:
        raise RuntimeError(f"Job {int(job_id)} was not found.")

    return _send_message(
        chat_id,
        format_job_card(job),
        reply_markup=build_keyboard(job),
    )


def _navigation_keyboard(
    bucket_key: str,
    offset: int,
    shown: int,
    total: int,
) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    navigation: list[dict[str, str]] = []

    previous_offset = max(0, offset - PAGE_SIZE)
    next_offset = offset + shown

    if offset > 0:
        navigation.append(
            {
                "text": "⬅️ Previous",
                "callback_data": f"cc:sc:b:{bucket_key}:{previous_offset}",
            }
        )
    if next_offset < total:
        navigation.append(
            {
                "text": "Next ➡️",
                "callback_data": f"cc:sc:b:{bucket_key}:{next_offset}",
            }
        )
    if navigation:
        rows.append(navigation)

    rows.append(
        [
            {"text": "📊 Score ranges", "callback_data": "cc:sc:menu"},
            {"text": "✖️ Close", "callback_data": "cc:sc:close"},
        ]
    )
    return {"inline_keyboard": rows}


def send_bucket_page(
    chat_id: int,
    bucket_key: str,
    offset: int = 0,
) -> dict[str, Any]:
    bucket = _bucket(bucket_key)
    offset = max(0, int(offset or 0))
    job_ids, total = load_bucket_job_ids(bucket_key, offset)

    # AADIL_SCORECARD_PAGE_HARD_FUSE_V2
    # One bucket-page invocation can never emit more than PAGE_SIZE unique jobs.
    job_ids = list(dict.fromkeys(job_ids))[:PAGE_SIZE]

    if total <= 0:
        _send_message(
            chat_id,
            (
                f"{bucket['emoji']} <b>Hunter score {html.escape(str(bucket['label']))}</b>\n\n"
                "No job cards were delivered in this score range during the past 24 hours."
            ),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "📊 Back to score ranges", "callback_data": "cc:sc:menu"}]
                ]
            },
        )
        return {
            "success": True,
            "message": f"No delivered jobs in {bucket['label']}.",
            "sent": [],
            "total": 0,
        }

    start_number = offset + 1
    end_number = min(offset + len(job_ids), total)
    page_number = (offset // PAGE_SIZE) + 1
    page_count = max(1, math.ceil(total / PAGE_SIZE))

    _send_message(
        chat_id,
        (
            f"{bucket['emoji']} <b>Hunter score {html.escape(str(bucket['label']))}</b>\n"
            f"Showing <b>{start_number}–{end_number}</b> of <b>{total}</b>\n"
            f"Page <b>{page_number}/{page_count}</b>\n"
            "Sorted by highest Hunter score, then latest Telegram delivery."
        ),
    )

    sent: list[dict[str, int]] = []
    failures: list[dict[str, Any]] = []
    for job_id in job_ids:
        try:
            message_id = _send_history_job_card(job_id, chat_id)
            sent.append({"job_id": int(job_id), "message_id": int(message_id)})
        except Exception as error:
            failures.append({"job_id": int(job_id), "error": str(error)})

    _send_message(
        chat_id,
        (
            f"📄 Finished page <b>{page_number}/{page_count}</b> · "
            f"opened <b>{len(sent)}</b> card(s)"
            + (f" · <b>{len(failures)}</b> failed" if failures else "")
        ),
        reply_markup=_navigation_keyboard(
            bucket_key,
            offset,
            len(job_ids),
            total,
        ),
    )

    return {
        "success": bool(sent) or not job_ids,
        "message": (
            f"Opened {len(sent)} job card(s) from Hunter score {bucket['label']}."
            + (f" {len(failures)} failed." if failures else "")
        ),
        "sent": sent,
        "failures": failures,
        "total": total,
        "offset": offset,
    }


def send_score_explanation(chat_id: int, job_id: int) -> dict[str, Any]:
    job = load_job(int(job_id))
    if not job:
        return {
            "success": False,
            "message": f"Job {int(job_id)} was not found.",
        }

    message_id = _send_message(
        chat_id,
        format_score_explanation(job),
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "📊 Past 24h scorecards", "callback_data": "cc:sc:menu"},
                    {"text": "🔁 Reopen job card", "callback_data": f"cc:sc:job:{int(job_id)}"},
                ]
            ]
        },
    )
    return {
        "success": True,
        "message": f"Score explanation sent for job {int(job_id)}.",
        "message_id": message_id,
    }


def reopen_job_card(chat_id: int, job_id: int) -> dict[str, Any]:
    message_id = _send_history_job_card(int(job_id), int(chat_id))
    return {
        "success": True,
        "message": f"Job {int(job_id)} reopened.",
        "message_id": message_id,
    }


def handle_scorecard_callback(
    callback_data: str,
    chat_id: int,
) -> tuple[bool, str, bool]:
    data = str(callback_data or "").strip()

    if data in {"cc:scorecards", "cc:sc:menu"}:
        result = send_scorecard_menu(int(chat_id))
        return True, str(result["message"]), False

    if data == "cc:sc:close":
        return True, "24-hour scorecard browser closed.", False

    parts = data.split(":")

    if len(parts) == 5 and parts[:3] == ["cc", "sc", "b"]:
        bucket_key = parts[3]
        try:
            offset = int(parts[4])
        except ValueError:
            return False, "Invalid scorecard page.", True
        try:
            result = send_bucket_page(int(chat_id), bucket_key, offset)
        except ValueError as error:
            return False, str(error), True
        return bool(result.get("success")), str(result.get("message")), not bool(result.get("success"))

    if len(parts) == 4 and parts[:3] == ["cc", "sc", "why"]:
        try:
            job_id = int(parts[3])
        except ValueError:
            return False, "Invalid job ID.", True
        result = send_score_explanation(int(chat_id), job_id)
        return bool(result.get("success")), str(result.get("message")), not bool(result.get("success"))

    if len(parts) == 4 and parts[:3] == ["cc", "sc", "job"]:
        try:
            job_id = int(parts[3])
        except ValueError:
            return False, "Invalid job ID.", True
        try:
            result = reopen_job_card(int(chat_id), job_id)
        except Exception as error:
            return False, f"Could not reopen job {job_id}: {error}", True
        return True, str(result["message"]), False

    return False, "Unsupported scorecard action.", True


def _allowed_chat_id() -> int:
    from app.telegram_client import CHAT_ID
    try:
        return int(str(CHAT_ID or "0").strip())
    except (TypeError, ValueError):
        return 0


async def scorecards_command(update: Any, context: Any) -> None:
    del context
    chat = getattr(update, "effective_chat", None)
    message = getattr(update, "effective_message", None)
    if chat is None or message is None:
        return

    chat_id = int(chat.id)
    if chat_id != _allowed_chat_id():
        await message.reply_text("Unauthorized Telegram chat.")
        return

    try:
        await asyncio.to_thread(send_scorecard_menu, chat_id)
    except Exception as error:
        await message.reply_text(f"Could not open the 24-hour scorecards: {error}")


async def last24h_command(update: Any, context: Any) -> None:
    await scorecards_command(update, context)


def self_test() -> dict[str, Any]:
    counts = load_24h_counts()
    sample = None
    for bucket in reversed(BUCKETS):
        ids, total = load_bucket_job_ids(str(bucket["key"]), 0, 1)
        if ids:
            job = load_job(ids[0])
            sample = {
                "bucket": bucket["key"],
                "total": total,
                "job_id": ids[0],
                "compact_lines": compact_score_reason_lines(job or {}),
                "explanation_length": len(format_score_explanation(job or {})),
            }
            break
    return {
        "marker": FEATURE_MARKER,
        "window_hours": WINDOW_HOURS,
        "page_size": PAGE_SIZE,
        "counts": counts,
        "total": sum(counts.values()),
        "sample": sample,
    }
