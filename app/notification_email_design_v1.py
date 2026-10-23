"""Responsive, evidence-specific MUNSHI application email notifications.

Only approved MUNSHI notification events are rendered. Source data are escaped,
no ATS verification artifacts enter the message and no third-party tracking
assets are loaded. The plain-text copy remains available for every email.
"""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Mapping, Any

from app import database
from app.job_destination_v1 import source_platform

DASHBOARD = "https://dashboard.munshi.systems/"
EVENTS = {
    "SUBMISSION_VERIFIED": ("Application submitted", "MUNSHI recorded a verified submission receipt for this application.", "#2369a5", "SUBMISSION"),
    "EMPLOYER_EMAIL_CONFIRMED": ("Employer confirmation received", "An application-confirmation email was matched to your job record. MUNSHI has recorded the evidence for you.", "#167450", "CONFIRMATION"),
    "RECRUITER": ("Recruiter update", "A recruiter-related message was matched to this application.", "#2369a5", "COMMUNICATION"),
    "INTERVIEW": ("Interview update", "An interview-related message was matched to this application. Review the original message for next steps.", "#2369a5", "COMMUNICATION"),
    "ASSESSMENT": ("Assessment update", "An assessment-related message was matched to this application. Review the original message for deadlines.", "#7057a2", "COMMUNICATION"),
    "REJECTION": ("Application update", "A status-change message was matched to this application. Review the original communication for details.", "#9c5345", "COMMUNICATION"),
    "OFFER": ("Offer-related update", "A potential offer-related message was matched to this application. Review the original message and verify the sender before acting.", "#167450", "COMMUNICATION"),
}


def _plain(value: object, length: int = 170) -> str:
    return " ".join(str(value or "").split())[:length]


def _job(message: Mapping[str, Any]) -> dict[str, str]:
    """Read display-only canonical job context; never alter receipt or identity."""
    try:
        job_id = int(message["job_id"])
        if job_id <= 0:
            return {}
        path = Path(database.DB_PATH).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(path, uri=True, timeout=3) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT company_name,title,source FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            return {}
        return {"company": _plain(row[0]), "title": _plain(row[1]),
                "origin": source_platform({"source": _plain(row[2], 60)})}
    except (ValueError, TypeError, KeyError, sqlite3.Error, OSError):
        return {}


def notification_email(
    message: Mapping[str, Any], *, job_context: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """Generate HTML and plain text from the same trusted outbox record."""
    kind = str(message.get("event_kind") or "")
    if kind not in EVENTS:
        raise ValueError("Notification type is unsupported")
    label, description, accent, category = EVENTS[kind]
    context = dict(job_context) if job_context is not None else _job(message)
    company = _plain(context.get("company") or "Employer", 160)
    position = _plain(context.get("title") or "Application", 200)
    origin = (source_platform({"source": context.get("origin")})
              if context.get("origin") else "")
    job_id = message.get("job_id")
    reference = str(int(job_id)) if str(job_id or "").isdigit() else None
    # A source job board is not an employer or ATS confirmation channel.
    info = f"Source: {origin}" if origin else "Source: MUNSHI application record"
    evidence = (
        "MUNSHI verified a submission receipt"
        if kind == "SUBMISSION_VERIFIED"
        else "MUNSHI correlated an application confirmation to this record"
        if kind == "EMPLOYER_EMAIL_CONFIRMED"
        else "MUNSHI correlated a received communication to this record"
    )
    plain = (
        f"{company} — {position}\n{label}\n\n"
        f"{description}\n\n{info}\nEvidence: {evidence}\n"
        + (f"Job reference: {reference}\n" if reference else "")
        + f"\nReview your application in MUNSHI: {DASHBOARD}\n\n"
        "This is an automated MUNSHI status notification. It is not an original "
        "message from the employer. Please check the original sender before "
        "responding to any request for information."
    )
    esc = lambda v: html.escape(v, quote=True)
    ref_html = (
        f'<tr><td style="padding:10px 0;color:#64748b;font-size:12px;">'
        f'Job reference</td><td style="padding:10px 0;text-align:right;'
        f'color:#334155;font-size:12px;">#{esc(reference)}</td></tr>'
        if reference else ""
    )
    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MUNSHI — {esc(label)}</title></head>
<body style="margin:0;padding:0;background:#f3f6fa;color:#17263f;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{esc(company)}
 — {esc(position)}. {esc(label)}. Evidence details inside MUNSHI.</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0"
style="width:100%;background:#f3f6fa;"><tr><td align="center" style="padding:32px 14px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"
style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e5ebf3;
border-radius:16px;overflow:hidden;">
<tr><td style="background:#112740;padding:24px 30px;">
<div style="font-size:23px;line-height:27px;font-weight:800;letter-spacing:1.6px;color:#ffffff;">MUNSHI</div>
<div style="font-size:11px;letter-spacing:1.5px;color:#c7d8e7;margin-top:6px;">APPLICATION INTELLIGENCE</div>
</td></tr>
<tr><td style="padding:30px 30px 12px;">
<div style="color:{accent};font-size:11px;font-weight:800;letter-spacing:1.5px;">{category} UPDATE</div>
<h1 style="font-size:27px;line-height:1.2;letter-spacing:-0.5px;margin:10px 0 13px;color:#152941;">{esc(label)}</h1>
<p style="font-size:15px;line-height:1.7;color:#475569;margin:0;">{esc(description)}</p>
</td></tr>
<tr><td style="padding:14px 30px 22px;">
<table role="presentation" style="width:100%;background:#f6f9fc;border:1px solid #e6edf5;
border-left:4px solid {accent};border-radius:10px;" cellpadding="0" cellspacing="0">
<tr><td style="padding:20px 22px;">
<div style="font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:#64748b;">APPLICATION</div>
<div style="font-size:20px;line-height:1.35;font-weight:750;color:#1b304a;margin-top:6px;">{esc(position)}</div>
<div style="font-size:15px;color:#425872;margin-top:7px;">{esc(company)}</div>
</td></tr></table></td></tr>
<tr><td style="padding:0 30px 24px;">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
<tr><td colspan="2" style="padding:0 0 9px;color:#1e314c;font-size:14px;font-weight:700;">Verification details</td></tr>
<tr><td style="padding:10px 0;border-top:1px solid #edf1f6;color:#64748b;font-size:12px;">Record</td>
<td style="padding:10px 0;border-top:1px solid #edf1f6;text-align:right;color:#334155;font-size:12px;">{esc(info)}</td></tr>
<tr><td colspan="2" style="padding:10px 0;border-top:1px solid #edf1f6;color:#475569;font-size:12px;line-height:1.5;">{esc(evidence)}</td></tr>
{ref_html}</table></td></tr>
<tr><td style="padding:0 30px 30px;">
<a href="{DASHBOARD}" style="display:inline-block;background:#173c65;color:#ffffff;
padding:14px 23px;border-radius:9px;text-decoration:none;font-size:14px;font-weight:700;">Review in MUNSHI →</a>
</td></tr>
<tr><td style="padding:20px 30px;border-top:1px solid #e9eef5;background:#fafbfd;">
<p style="font-size:12px;line-height:1.7;color:#64748b;margin:0 0 9px;">
This is an automated MUNSHI status update, not an original email from the employer.
Check the original sender before responding to requests for personal information.</p>
<p style="font-size:11px;color:#94a3b8;margin:0;">MUNSHI · Application tracking</p>
</td></tr></table></td></tr></table></body></html>"""
    return html_body, plain
