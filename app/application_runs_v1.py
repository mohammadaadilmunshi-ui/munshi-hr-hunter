from __future__ import annotations

import html
import json
import re
import sqlite3
from typing import Any

from app.database import get_connection, get_setting
from app.job_detail import build_manual_job_text

VERSION = "telegram-unified-ats-runs-v1.0.0"


def _contract() -> dict[str, Any]:
    return dict(get_setting("downstream_contract", {}) or {})


def _page_size() -> int:
    return max(1, int(_contract().get("telegram_application_run_page_size") or 8))


def _description_chunk_size() -> int:
    return max(500, int(_contract().get("telegram_description_chunk_size") or 3200))


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {
        row[1]
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def hard_work_authorization_block(job: dict[str, Any]) -> tuple[bool, str]:
    text = " ".join(
        _clean(job.get(key))
        for key in (
            "work_authorization",
            "work_authorization_notes",
            "cpt_opt_sponsorship_notes",
            "description_raw",
            "description",
            "job_description",
            "manual_job_text",
        )
    ).casefold()

    patterns = tuple(
        str(value).casefold()
        for value in (_contract().get("dispatch_hard_authorization_phrases") or [])
        if str(value).strip()
    )
    matched = next((pattern for pattern in patterns if pattern in text), "")
    cpt_trapdoor = int(job.get("cpt_trapdoor") or 0) == 1
    blocked = bool(matched or cpt_trapdoor)
    reason = matched or ("cpt_trapdoor" if cpt_trapdoor else "")
    return blocked, reason


def enhance_dispatch_payload(
    payload: dict[str, Any],
    job: dict[str, Any],
    queue: dict[str, Any],
) -> dict[str, Any]:
    blocked, reason = hard_work_authorization_block(job)
    if blocked:
        raise ValueError(
            "WORK_AUTHORIZATION_BLOCKED: This posting is restricted to "
            "U.S. citizens/permanent residents or is marked as a CPT trapdoor. "
            f"Evidence={reason}"
        )

    result = dict(payload)
    canonical = build_manual_job_text(job)
    dispatch_mode = _clean(queue.get("dispatch_mode") or result.get("dispatch_mode"))
    entry_path = {
        "telegram_manual": "telegram_manual",
        "auto_top_match": "automatic_top_match",
        "manual": "manual",
    }.get(dispatch_mode, dispatch_mode or "manual")

    full_description = _clean(
        job.get("description_raw")
        or job.get("description")
        or job.get("job_description")
        or canonical
    )

    contract = _contract()
    result.update(
        {
            "manual_job_text": canonical,
            "full_job_description": full_description,
            "job_description": full_description,
            "description_raw": full_description,
            "queue_id": int(queue.get("id") or 0),
            "entry_path": entry_path,
            "dispatch_mode": dispatch_mode,
            "source_adapter": _clean(job.get("source") or job.get("provider")),
            "ats_pipeline_required": True,
            "ats_target_score": int(contract.get("ats_target_score") or 0),
            "ats_engine_required": str(contract.get("ats_engine_required") or ""),
            "ats_final_gate_required": str(contract.get("ats_final_gate_required") or ""),
            "writer_score_retention_required": bool(contract.get("writer_score_retention_required")),
            "full_description_required": bool(contract.get("full_description_required")),
            "application_run_tracking_version": str(contract.get("application_run_tracking_version") or VERSION),
        }
    )
    return result


def normalize_callback_status(
    status: str,
    score: float | int | None,
    *,
    gate_status: str | None = None,
    evidence_integrity: float | int | None = None,
    missing_verified_terms: Any = None,
    placement_gaps: Any = None,
    unsupported_market_gaps: Any = None,
) -> str:
    original = _clean(status) or "completed"
    if original not in {"application_ready", "final_ready"}:
        return original

    score_value = float(score or 0)
    gate = _clean(gate_status)
    evidence = float(evidence_integrity if evidence_integrity is not None else 100)

    def empty(value: Any) -> bool:
        if value in (None, "", [], {}):
            return True
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return True
            try:
                parsed = json.loads(text)
            except Exception:
                return False
            return parsed in (None, "", [], {})
        return False

    contract = _contract()
    target_score = float(contract.get("ats_target_score") or 0)
    ready_gate = str(contract.get("ats_final_gate_required") or "")
    strict_ready = (
        score_value >= target_score
        and (not gate or gate == ready_gate or gate.startswith("final_ready"))
        and evidence >= 100
        and empty(missing_verified_terms)
        and empty(placement_gaps)
        and empty(unsupported_market_gaps)
    )
    return "application_ready" if strict_ready else "ats_review_required"


def _job(connection: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
    return dict(row) if row is not None else None


def canonical_job_text(job_id: int) -> str:
    connection = get_connection()
    try:
        job = _job(connection, job_id)
    finally:
        connection.close()
    if not job:
        raise RuntimeError(f"Job {job_id} was not found.")
    return build_manual_job_text(job)


def _latest_callback_audit(connection: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    if not _table_exists(connection, "events"):
        return {}
    rows = connection.execute(
        """
        SELECT payload_json
        FROM events
        WHERE job_id=? AND event_type='n8n_status_callback'
        ORDER BY id DESC
        LIMIT 5
        """,
        (int(job_id),),
    ).fetchall()
    for row in rows:
        try:
            data = json.loads(row[0] or "{}")
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def latest_result_for_job(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "n8n_results"):
            return None
        row = connection.execute(
            """
            SELECT r.*, j.company_name, j.title, j.source, j.hunter_score
            FROM n8n_results AS r
            JOIN jobs AS j ON j.id=r.job_id
            WHERE r.job_id=?
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (int(job_id),),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["callback_audit"] = _latest_callback_audit(connection, job_id)
        return result
    finally:
        connection.close()


def resolve_job_id_from_queue(queue_id: int) -> int | None:
    connection = get_connection()
    try:
        if not _table_exists(connection, "n8n_dispatch_queue"):
            return None
        row = connection.execute(
            "SELECT job_id FROM n8n_dispatch_queue WHERE id=?",
            (int(queue_id),),
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        connection.close()


def application_keyboard(job_id: int) -> list[list[dict[str, str]]]:
    return [
        [
            {
                "text": "📋 Full Job Description",
                "callback_data": f"app:desc:{int(job_id)}:0",
            },
            {
                "text": "🧪 ATS Audit",
                "callback_data": f"app:audit:{int(job_id)}",
            },
        ],
        [
            {
                "text": "📚 All n8n Runs",
                "callback_data": "app:runs:0",
            }
        ],
    ]


def _merge_keyboard(existing: Any, rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    markup: dict[str, Any]
    if isinstance(existing, str):
        try:
            parsed = json.loads(existing)
        except Exception:
            parsed = {}
        markup = parsed if isinstance(parsed, dict) else {}
    elif isinstance(existing, dict):
        markup = dict(existing)
    else:
        markup = {}
    keyboard = list(markup.get("inline_keyboard") or [])
    existing_callbacks = {
        button.get("callback_data")
        for row in keyboard
        for button in row
        if isinstance(button, dict)
    }
    for row in rows:
        filtered = [
            button
            for button in row
            if button.get("callback_data") not in existing_callbacks
        ]
        if filtered:
            keyboard.append(filtered)
    markup["inline_keyboard"] = keyboard
    return markup


def decorate_job_keyboard(existing: Any, job_id: int) -> dict[str, Any]:
    return _merge_keyboard(existing, application_keyboard(job_id))


def decorate_completion_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    text = str(result.get("text") or "")
    if "n8n application package finished" not in text.casefold():
        return result
    queue_match = re.search(r"Queue ID:\s*(\d+)", text, flags=re.I)
    if not queue_match:
        return result
    job_id = resolve_job_id_from_queue(int(queue_match.group(1)))
    if not job_id:
        return result
    result["reply_markup"] = json.dumps(
        decorate_job_keyboard(result.get("reply_markup"), job_id),
        ensure_ascii=False,
    )
    return result


def _send_message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    from app.telegram_client import telegram_request

    payload: dict[str, Any] = {
        "chat_id": str(int(chat_id)),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    telegram_request("sendMessage", payload)


def send_full_description(chat_id: int, job_id: int, page: int = 0) -> dict[str, Any]:
    text = canonical_job_text(job_id)
    chunk_size = _description_chunk_size()
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or ["No description available."]
    page = max(0, min(int(page), len(chunks) - 1))
    body = (
        f"<b>📋 Full Job Description</b>\n"
        f"<b>Job ID:</b> {int(job_id)}\n"
        f"<b>Page:</b> {page + 1}/{len(chunks)}\n\n"
        f"<pre>{html.escape(chunks[page])}</pre>"
    )
    nav: list[dict[str, str]] = []
    if page > 0:
        nav.append({"text": "⬅️ Previous", "callback_data": f"app:desc:{job_id}:{page - 1}"})
    if page + 1 < len(chunks):
        nav.append({"text": "Next ➡️", "callback_data": f"app:desc:{job_id}:{page + 1}"})
    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.extend(application_keyboard(job_id)[1:])
    _send_message(chat_id, body, {"inline_keyboard": keyboard})
    return {"success": True, "job_id": job_id, "page": page, "pages": len(chunks)}


def _load_runs(offset: int) -> tuple[list[dict[str, Any]], bool]:
    connection = get_connection()
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "n8n_results"):
            return [], False
        queue_join = ""
        queue_fields = "NULL AS queue_id, NULL AS dispatch_mode, NULL AS request_id"
        if _table_exists(connection, "n8n_dispatch_queue"):
            queue_join = """
            LEFT JOIN n8n_dispatch_queue AS q
              ON q.id=(SELECT MAX(q2.id) FROM n8n_dispatch_queue q2 WHERE q2.job_id=r.job_id)
            """
            queue_fields = "q.id AS queue_id, q.dispatch_mode, q.request_id"
        query = f"""
            SELECT r.id AS result_id, r.job_id, r.send_mode, r.n8n_status,
                   r.final_ats_score, r.resume_doc_url, r.resume_pdf_url,
                   r.cover_letter_doc_url, r.google_sheet_url,
                   r.recruiter_found, r.outreach_draft_created,
                   r.sent_at, r.completed_at,
                   j.company_name, j.title, j.source, j.hunter_score,
                   {queue_fields}
            FROM n8n_results AS r
            JOIN jobs AS j ON j.id=r.job_id
            {queue_join}
            ORDER BY r.id DESC
            LIMIT ? OFFSET ?
        """
        page_size = _page_size()
        rows = [dict(row) for row in connection.execute(query, (page_size + 1, int(offset))).fetchall()]
        return rows[:page_size], len(rows) > page_size
    finally:
        connection.close()


def send_application_runs(chat_id: int, offset: int = 0) -> dict[str, Any]:
    offset = max(0, int(offset))
    rows, has_more = _load_runs(offset)
    if not rows:
        _send_message(chat_id, "<b>📚 n8n Application Runs</b>\n\nNo completed application runs were found.")
        return {"success": True, "count": 0, "offset": offset}

    lines = ["<b>📚 n8n Application Runs</b>", ""]
    keyboard: list[list[dict[str, str]]] = []
    for row in rows:
        score = row.get("final_ats_score")
        score_text = "—" if score is None else f"{float(score):.0f}"
        mode = _clean(row.get("dispatch_mode") or row.get("send_mode") or "manual")
        lines.extend(
            [
                f"<b>#{int(row['result_id'])} · {html.escape(_clean(row.get('company_name')))}</b>",
                f"{html.escape(_clean(row.get('title')))}",
                f"Path: {html.escape(mode)} · ATS: {score_text} · Status: {html.escape(_clean(row.get('n8n_status')))}",
                "",
            ]
        )
        keyboard.append(
            [
                {
                    "text": f"#{int(row['result_id'])} Details",
                    "callback_data": f"app:run:{int(row['result_id'])}",
                },
                {
                    "text": "Full JD",
                    "callback_data": f"app:desc:{int(row['job_id'])}:0",
                },
            ]
        )

    nav: list[dict[str, str]] = []
    if offset > 0:
        nav.append({"text": "⬅️ Newer", "callback_data": f"app:runs:{max(0, offset - _page_size())}"})
    if has_more:
        nav.append({"text": "Older ➡️", "callback_data": f"app:runs:{offset + _page_size()}"})
    if nav:
        keyboard.append(nav)

    _send_message(chat_id, "\n".join(lines), {"inline_keyboard": keyboard})
    return {"success": True, "count": len(rows), "offset": offset}


def _result_by_id(result_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "n8n_results"):
            return None
        row = connection.execute(
            """
            SELECT r.*, j.company_name, j.title, j.source, j.hunter_score
            FROM n8n_results r JOIN jobs j ON j.id=r.job_id
            WHERE r.id=?
            """,
            (int(result_id),),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["callback_audit"] = _latest_callback_audit(connection, int(result["job_id"]))
        return result
    finally:
        connection.close()


def _link(label: str, url: Any) -> str:
    value = _clean(url)
    return f'<a href="{html.escape(value, quote=True)}">{html.escape(label)}</a>' if value else f"{html.escape(label)}: unavailable"


def send_run_detail(chat_id: int, result_id: int) -> dict[str, Any]:
    row = _result_by_id(result_id)
    if not row:
        raise RuntimeError(f"Application result {result_id} was not found.")
    audit = row.get("callback_audit") or {}
    score = float(row.get("final_ats_score") or 0)
    text = "\n".join(
        [
            f"<b>📦 Application Run #{int(result_id)}</b>",
            f"<b>{html.escape(_clean(row.get('company_name')))}</b>",
            html.escape(_clean(row.get("title"))),
            "",
            f"Entry path: {html.escape(_clean(audit.get('entry_path') or row.get('send_mode')))}",
            f"Adapter: {html.escape(_clean(audit.get('source_adapter') or row.get('source')))}",
            f"Status: {html.escape(_clean(row.get('n8n_status')))}",
            f"ATS score: {score:.0f}",
            f"ATS engine: {html.escape(_clean(audit.get('ats_engine_version')) or 'not recorded')}",
            f"Final gate: {html.escape(_clean(audit.get('ats_gate_status')) or 'not recorded')}",
            f"Evidence integrity: {html.escape(_clean(audit.get('evidence_integrity')) or 'not recorded')}",
            "",
            _link("Resume DOCX / Google Doc", row.get("resume_doc_url")),
            _link("Resume PDF", row.get("resume_pdf_url")),
            _link("Cover Letter", row.get("cover_letter_doc_url")),
            _link("Google Sheet", row.get("google_sheet_url")),
        ]
    )
    _send_message(chat_id, text, {"inline_keyboard": application_keyboard(int(row["job_id"]))})
    return {"success": True, "result_id": result_id, "job_id": int(row["job_id"])}


def send_ats_audit(chat_id: int, job_id: int) -> dict[str, Any]:
    row = latest_result_for_job(job_id)
    if not row:
        _send_message(chat_id, f"<b>🧪 ATS Audit</b>\n\nNo n8n result exists for Job ID {int(job_id)}.")
        return {"success": True, "job_id": job_id, "found": False}
    audit = row.get("callback_audit") or {}
    lines = [
        "<b>🧪 ATS Audit</b>",
        f"<b>{html.escape(_clean(row.get('company_name')))}</b>",
        html.escape(_clean(row.get("title"))),
        "",
        f"Final ATS: {float(row.get('final_ats_score') or 0):.0f}",
        f"Status: {html.escape(_clean(row.get('n8n_status')))}",
        f"Engine: {html.escape(_clean(audit.get('ats_engine_version')) or 'not recorded')}",
        f"Gate: {html.escape(_clean(audit.get('ats_gate_status')) or 'not recorded')}",
        f"Evidence integrity: {html.escape(_clean(audit.get('evidence_integrity')) or 'not recorded')}",
        f"Missing verified: {html.escape(_clean(audit.get('missing_verified_terms')) or 'none')}",
        f"Placement gaps: {html.escape(_clean(audit.get('placement_gaps')) or 'none')}",
        f"Unsupported gaps: {html.escape(_clean(audit.get('unsupported_market_gaps')) or 'none')}",
    ]
    _send_message(chat_id, "\n".join(lines), {"inline_keyboard": application_keyboard(job_id)})
    return {"success": True, "job_id": job_id, "found": True}


def handle_application_callback(callback_data: str, chat_id: int) -> tuple[bool, str, bool]:
    try:
        parts = str(callback_data or "").split(":")
        if len(parts) < 2 or parts[0] != "app":
            return False, "Invalid application action.", True
        action = parts[1]
        if action == "runs":
            send_application_runs(chat_id, int(parts[2]) if len(parts) > 2 else 0)
            return True, "Application runs opened.", False
        if action == "desc" and len(parts) >= 4:
            send_full_description(chat_id, int(parts[2]), int(parts[3]))
            return True, "Full job description opened.", False
        if action == "audit" and len(parts) >= 3:
            send_ats_audit(chat_id, int(parts[2]))
            return True, "ATS audit opened.", False
        if action == "run" and len(parts) >= 3:
            send_run_detail(chat_id, int(parts[2]))
            return True, "Application run opened.", False
        return False, "Unsupported application action.", True
    except Exception as error:
        return False, f"Application action failed: {error}", True


def self_test() -> dict[str, Any]:
    eligible = {
        "id": 1,
        "company_name": "Example",
        "title": "Recruiting Coordinator",
        "description_raw": "Coordinate recruiting, sourcing, and reporting.",
        "job_fingerprint": "x" * 64,
        "source": "JobSpy/linkedin",
    }
    payload = enhance_dispatch_payload(
        {"manual_job_text": "old"},
        eligible,
        {"id": 7, "dispatch_mode": "telegram_manual"},
    )
    blocked, _ = hard_work_authorization_block(
        {"description_raw": "Requirements: U.S. citizen or Permanent Resident."}
    )
    checks = {
        "canonical_manual_text": "Job Title:" in payload.get("manual_job_text", ""),
        "full_description": bool(payload.get("full_job_description")),
        "telegram_entry_path": payload.get("entry_path") == "telegram_manual",
        "ats_target_95": payload.get("ats_target_score") == 95,
        "low_score_not_ready": normalize_callback_status("application_ready", 70) == "ats_review_required",
        "high_score_ready": normalize_callback_status(
            "application_ready",
            99,
            gate_status=str(_contract().get("ats_final_gate_required") or ""),
            evidence_integrity=100,
            missing_verified_terms="",
            placement_gaps=[],
            unsupported_market_gaps="",
        ) == "application_ready",
        "citizen_only_blocked": blocked,
        "keyboard_buttons": len(application_keyboard(1)) == 2,
    }
    return {"success": all(checks.values()), "checks": checks, "version": VERSION}

# AADIL_LIVE_TELEGRAM_ATS_RECOVERY_V2
VERSION = "telegram-live-ats-recovery-v2.0.0"


def _canonical_adapter(value: Any) -> str:
    text = _clean(value)
    lower = text.casefold()
    if not text:
        return ""
    if "serpapi" in lower and "google" in lower:
        return "Google Jobs · SerpAPI"
    mapping = (
        ("linkedin", "LinkedIn Jobs (JobSpy)"),
        ("indeed", "Indeed Jobs (JobSpy)"),
        ("greenhouse", "Greenhouse"),
        ("smartrecruiters", "SmartRecruiters"),
        ("ashby", "Ashby"),
        ("lever", "Lever"),
        ("dice", "Dice"),
        ("google", "Google Jobs"),
        ("jobspy", "JobSpy"),
        ("bamboo", "BambooHR"),
        ("jobvite", "Jobvite"),
        ("recruitee", "Recruitee"),
        ("teamtailor", "Teamtailor"),
        ("workable", "Workable"),
        ("icims", "iCIMS"),
        ("adzuna", "Adzuna"),
        ("apify", "Apify"),
    )
    for token, name in mapping:
        if token in lower:
            return name
    return text.split("/", 1)[0].strip() or text


def _walk_source_values(value: Any) -> list[str]:
    output: list[str] = []

    def walk(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                walk(child, str(child_key))
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child, key)
        elif isinstance(item, str):
            if any(
                token in key.casefold()
                for token in ("source", "adapter", "provider", "site_name")
            ):
                output.append(item)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = value
    else:
        parsed = value
    walk(parsed)
    return output


def adapter_names_for_job(job_id: int) -> list[str]:
    connection = get_connection()
    connection.row_factory = sqlite3.Row
    raw: list[str] = []
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if row:
            data = dict(row)
            for key in ("source", "source_name", "provider", "adapter"):
                if data.get(key):
                    raw.append(str(data[key]))

        if _table_exists(connection, "events"):
            for event in connection.execute(
                """
                SELECT payload_json
                FROM events
                WHERE job_id=?
                ORDER BY id DESC
                LIMIT 100
                """,
                (int(job_id),),
            ).fetchall():
                raw.extend(_walk_source_values(event[0] or ""))

        if (
            _table_exists(connection, "telegram_source_run_jobs")
            and _table_exists(connection, "telegram_source_runs")
        ):
            for source_row in connection.execute(
                """
                SELECT r.source_name
                FROM telegram_source_run_jobs j
                JOIN telegram_source_runs r ON r.id=j.run_id
                WHERE j.job_id=?
                ORDER BY r.id DESC
                """,
                (int(job_id),),
            ).fetchall():
                if source_row[0]:
                    raw.append(str(source_row[0]))
    finally:
        connection.close()

    adapters: list[str] = []
    for value in raw:
        canonical = _canonical_adapter(value)
        if canonical and canonical not in adapters:
            adapters.append(canonical)
    return adapters


def enabled_adapter_names() -> list[str]:
    connection = get_connection()
    try:
        if not _table_exists(connection, "source_health"):
            return []
        rows = connection.execute(
            """
            SELECT source_name
            FROM source_health
            WHERE enabled=1
            ORDER BY source_tier, source_name
            """
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        connection.close()


def application_keyboard(job_id: int) -> list[list[dict[str, str]]]:
    job_id = int(job_id)
    return [
        [
            {
                "text": "📋 Full Job Description",
                "callback_data": f"app:desc:{job_id}:0",
            },
            {
                "text": "🧪 ATS Audit",
                "callback_data": f"app:audit:{job_id}",
            },
        ],
        [
            {
                "text": "🔌 Adapters",
                "callback_data": f"app:adapters:{job_id}",
            },
            {
                "text": "📚 All n8n Runs",
                "callback_data": "app:runs:0",
            },
        ],
        [
            {
                "text": "✅ Applied Jobs",
                "callback_data": "app:applied:0",
            }
        ],
    ]


_v2_previous_completion_decorator = decorate_completion_payload


def decorate_completion_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = _v2_previous_completion_decorator(payload)
    text = str(result.get("text") or "")
    if "n8n application package finished" not in text.casefold() and (
        "n8n package requires ats review" not in text.casefold()
    ):
        return result

    queue_match = re.search(r"Queue ID:\s*(\d+)", text, flags=re.I)
    if not queue_match:
        return result
    job_id = resolve_job_id_from_queue(int(queue_match.group(1)))
    if not job_id:
        return result

    stored = latest_result_for_job(job_id) or {}
    audit = stored.get("callback_audit") or {}
    score = float(stored.get("final_ats_score") or 0)
    normalized = normalize_callback_status(
        str(stored.get("n8n_status") or ""),
        score,
        gate_status=audit.get("ats_gate_status"),
        evidence_integrity=audit.get("evidence_integrity"),
        missing_verified_terms=audit.get("missing_verified_terms"),
        placement_gaps=audit.get("placement_gaps"),
        unsupported_market_gaps=audit.get("unsupported_market_gaps"),
    )
    adapter_text = ", ".join(adapter_names_for_job(job_id)) or "Unknown"

    text = re.sub(
        r"(?m)^📌 Status:\s*.*$",
        f"📌 Status: {normalized}",
        text,
    )
    text = re.sub(
        r"(?m)^🎯 ATS score:\s*.*$",
        f"🎯 ATS score: {score:.1f}",
        text,
    )
    text = re.sub(
        r"(?m)^🔌 Adapter(?:\(s\))?:\s*.*$",
        "",
        text,
    ).strip()
    entry_match = re.search(r"(?m)^🚦 Entry path:.*$", text)
    adapter_line = f"🔌 Adapter(s): {adapter_text}"
    if entry_match:
        text = text[: entry_match.end()] + "\n" + adapter_line + text[entry_match.end() :]
    else:
        text = adapter_line + "\n" + text

    gate = _clean(audit.get("ats_gate_status")) or "not recorded"
    if "🛡 ATS gate:" not in text:
        text += f"\n🛡 ATS gate: {gate}"

    if normalized != "application_ready":
        text = re.sub(
            r"✅ n8n application package finished",
            "⚠️ n8n package requires ATS review",
            text,
            flags=re.I,
        )
        if "Not approved for application" not in text:
            text += (
                "\n\n⛔ Not approved for application until the verified "
                "ATS score is 95+ and the final gate passes."
            )

    result["text"] = text
    result["reply_markup"] = json.dumps(
        decorate_job_keyboard(result.get("reply_markup"), int(job_id)),
        ensure_ascii=False,
    )
    return result


def decorate_telegram_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = decorate_completion_payload(payload)
    text = str(result.get("text") or "")
    lower = text.casefold()
    markup_text = str(result.get("reply_markup") or "").casefold()
    is_control_panel = (
        all(
            token in lower
            for token in ("recent jobs", "status", "timers", "queue")
        )
        or all(
            token in markup_text
            for token in ("cc:recent", "cc:status", "cc:queue")
        )
        or all(
            token in markup_text
            for token in ("recent jobs", "timers", "queue")
        )
    )
    if is_control_panel:
        rows = [
            [
                {
                    "text": "✅ Applied Jobs",
                    "callback_data": "app:applied:0",
                },
                {
                    "text": "📚 All n8n Runs",
                    "callback_data": "app:runs:0",
                },
            ],
            [
                {
                    "text": "🔌 Enabled Adapters",
                    "callback_data": "app:adapters:0",
                }
            ],
        ]
        result["reply_markup"] = json.dumps(
            _merge_keyboard(result.get("reply_markup"), rows),
            ensure_ascii=False,
        )
    return result


def send_adapters_dashboard(chat_id: int, job_id: int = 0) -> dict[str, Any]:
    lines = ["<b>🔌 Adapter Dashboard</b>", ""]
    job_adapters: list[str] = []
    if int(job_id) > 0:
        job_adapters = adapter_names_for_job(int(job_id))
        lines.extend(
            [
                f"<b>Job ID:</b> {int(job_id)}",
                "<b>Found/reported by:</b>",
                *[f"• {html.escape(name)}" for name in job_adapters],
                "",
            ]
        )
    enabled = enabled_adapter_names()
    lines.extend(
        [
            "<b>Enabled adapters:</b>",
            *[f"• {html.escape(name)}" for name in enabled],
        ]
    )
    _send_message(
        chat_id,
        "\n".join(lines),
        {"inline_keyboard": application_keyboard(int(job_id or 1))[1:]},
    )
    return {
        "success": True,
        "job_adapters": job_adapters,
        "enabled_adapters": enabled,
    }


def _applied_rows(offset: int) -> tuple[list[dict[str, Any]], bool]:
    connection = get_connection()
    connection.row_factory = sqlite3.Row
    try:
        columns = _columns(connection, "jobs")
        predicates = ["lower(COALESCE(status, ''))='already_applied'"]
        if "already_applied" in columns:
            predicates.append("COALESCE(already_applied, 0)=1")
        rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT id, company_name, title, source, status,
                       hunter_score, updated_at
                FROM jobs
                WHERE {" OR ".join(predicates)}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (_page_size() + 1, max(0, int(offset))),
            ).fetchall()
        ]
        return rows[:_page_size()], len(rows) > _page_size()
    finally:
        connection.close()


def send_applied_jobs(chat_id: int, offset: int = 0) -> dict[str, Any]:
    offset = max(0, int(offset))
    rows, has_more = _applied_rows(offset)
    if not rows:
        _send_message(
            chat_id,
            "<b>✅ Applied Jobs</b>\n\nNo job is currently marked as already applied.",
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "📚 Application Runs",
                            "callback_data": "app:runs:0",
                        }
                    ]
                ]
            },
        )
        return {"success": True, "count": 0, "offset": offset}

    lines = ["<b>✅ Applied Jobs</b>", ""]
    keyboard: list[list[dict[str, str]]] = []
    for row in rows:
        adapters = adapter_names_for_job(int(row["id"]))
        lines.extend(
            [
                f"<b>#{int(row['id'])} · {html.escape(_clean(row.get('company_name')))}</b>",
                html.escape(_clean(row.get("title"))),
                "Adapter(s): " + html.escape(", ".join(adapters) or "Unknown"),
                "",
            ]
        )
        keyboard.append(
            [
                {
                    "text": f"#{int(row['id'])} Job",
                    "callback_data": f"app:desc:{int(row['id'])}:0",
                },
                {
                    "text": "ATS Audit",
                    "callback_data": f"app:audit:{int(row['id'])}",
                },
            ]
        )
    nav: list[dict[str, str]] = []
    if offset > 0:
        nav.append(
            {
                "text": "⬅️ Newer",
                "callback_data": f"app:applied:{max(0, offset - _page_size())}",
            }
        )
    if has_more:
        nav.append(
            {
                "text": "Older ➡️",
                "callback_data": f"app:applied:{offset + _page_size()}",
            }
        )
    if nav:
        keyboard.append(nav)
    keyboard.append(
        [
            {
                "text": "📚 All n8n Runs",
                "callback_data": "app:runs:0",
            }
        ]
    )
    _send_message(chat_id, "\n".join(lines), {"inline_keyboard": keyboard})
    return {"success": True, "count": len(rows), "offset": offset}


_v2_previous_ats_audit = send_ats_audit


def send_ats_audit(chat_id: int, job_id: int) -> dict[str, Any]:
    row = latest_result_for_job(job_id)
    if not row:
        return _v2_previous_ats_audit(chat_id, job_id)
    audit = row.get("callback_audit") or {}
    score = float(row.get("final_ats_score") or 0)
    normalized = normalize_callback_status(
        str(row.get("n8n_status") or ""),
        score,
        gate_status=audit.get("ats_gate_status"),
        evidence_integrity=audit.get("evidence_integrity"),
        missing_verified_terms=audit.get("missing_verified_terms"),
        placement_gaps=audit.get("placement_gaps"),
        unsupported_market_gaps=audit.get("unsupported_market_gaps"),
    )
    lines = [
        "<b>🧪 ATS Audit</b>",
        f"<b>{html.escape(_clean(row.get('company_name')))}</b>",
        html.escape(_clean(row.get("title"))),
        "",
        "Adapter(s): " + html.escape(", ".join(adapter_names_for_job(job_id)) or "Unknown"),
        f"Final ATS: {score:.0f}",
        f"Normalized status: {html.escape(normalized)}",
        f"Engine: {html.escape(_clean(audit.get('ats_engine_version')) or 'not recorded')}",
        f"Gate: {html.escape(_clean(audit.get('ats_gate_status')) or 'not recorded')}",
        f"Evidence integrity: {html.escape(_clean(audit.get('evidence_integrity')) or 'not recorded')}",
        f"Missing verified: {html.escape(_clean(audit.get('missing_verified_terms')) or 'none')}",
        f"Placement gaps: {html.escape(_clean(audit.get('placement_gaps')) or 'none')}",
        f"Unsupported gaps: {html.escape(_clean(audit.get('unsupported_market_gaps')) or 'none')}",
    ]
    _send_message(chat_id, "\n".join(lines), {"inline_keyboard": application_keyboard(job_id)})
    return {"success": True, "job_id": int(job_id), "normalized_status": normalized}


def handle_application_callback(callback_data: str, chat_id: int) -> tuple[bool, str, bool]:
    try:
        parts = str(callback_data or "").split(":")
        if len(parts) < 2 or parts[0] != "app":
            return False, "Invalid application action.", True
        action = parts[1]
        if action == "runs":
            send_application_runs(chat_id, int(parts[2]) if len(parts) > 2 else 0)
            return True, "Application runs opened.", False
        if action == "applied":
            send_applied_jobs(chat_id, int(parts[2]) if len(parts) > 2 else 0)
            return True, "Applied-jobs dashboard opened.", False
        if action == "adapters":
            send_adapters_dashboard(chat_id, int(parts[2]) if len(parts) > 2 else 0)
            return True, "Adapter dashboard opened.", False
        if action == "desc" and len(parts) >= 4:
            send_full_description(chat_id, int(parts[2]), int(parts[3]))
            return True, "Full job description opened.", False
        if action == "audit" and len(parts) >= 3:
            send_ats_audit(chat_id, int(parts[2]))
            return True, "ATS audit opened.", False
        if action == "run" and len(parts) >= 3:
            send_run_detail(chat_id, int(parts[2]))
            return True, "Application run opened.", False
        return False, "Unsupported application action.", True
    except Exception as error:
        return False, f"Application action failed: {error}", True


def self_test_v2() -> dict[str, Any]:
    low = normalize_callback_status(
        "application_ready",
        50,
        gate_status="placement_or_polish_review_required",
        evidence_integrity=100,
    )
    high = normalize_callback_status(
        "application_ready",
        99,
        gate_status=str(_contract().get("ats_final_gate_required") or ""),
        evidence_integrity=100,
        missing_verified_terms="",
        placement_gaps=[],
        unsupported_market_gaps="",
    )
    callbacks = {
        button.get("callback_data")
        for row in application_keyboard(24)
        for button in row
    }
    checks = {
        "version_v2": VERSION.endswith("v2.0.0"),
        "score_50_blocked": low == "ats_review_required",
        "score_99_ready": high == "application_ready",
        "full_description_button": "app:desc:24:0" in callbacks,
        "ats_audit_button": "app:audit:24" in callbacks,
        "adapter_button": "app:adapters:24" in callbacks,
        "runs_button": "app:runs:0" in callbacks,
        "applied_button": "app:applied:0" in callbacks,
    }
    return {"success": all(checks.values()), "checks": checks}

# AADIL_FORCE_RERUN_APPLICATION_BUTTON_V1
_aadil_application_keyboard_before_force_rerun_v1 = application_keyboard
_aadil_handle_application_callback_before_force_rerun_v1 = handle_application_callback


def application_keyboard(job_id: int) -> list[list[dict[str, str]]]:
    rows = _aadil_application_keyboard_before_force_rerun_v1(int(job_id))
    callbacks = {
        str(button.get("callback_data") or "")
        for row in rows
        for button in row
        if isinstance(button, dict)
    }
    callback = f"app:force:{int(job_id)}"
    if callback not in callbacks:
        try:
            from app.force_rerun_v1 import next_rerun_part
            part = int(next_rerun_part(int(job_id)))
        except Exception:
            part = 2
        rows.append([{
            "text": f"🔁 Force Rerun as Part {part}",
            "callback_data": callback,
        }])
    return rows


def handle_application_callback(
    callback_data: str,
    chat_id: int,
) -> tuple[bool, str, bool]:
    parts = str(callback_data or "").split(":")
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "force":
        try:
            job_id = int(parts[2])
            from app.force_rerun_v1 import request_force_rerun
            result = request_force_rerun(job_id, int(chat_id))
            message = str(result.get("message") or "Force rerun request processed.")
            try:
                _send_message(int(chat_id), message)
            except Exception:
                pass
            return bool(result.get("success")), message, not bool(result.get("success"))
        except Exception as error:
            return False, f"Force rerun failed: {error}", True
    return _aadil_handle_application_callback_before_force_rerun_v1(
        callback_data,
        chat_id,
    )
