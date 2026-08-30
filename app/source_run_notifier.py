from __future__ import annotations

import argparse
import html
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.database import (
    get_connection,
    get_setting,
)
from app.telegram_client import (
    CHAT_ID,
    telegram_request,
)


SETTING_KEY = "source_run_notifications"

SENT_EVENT_TYPE = (
    "source_run_telegram_notification_sent"
)

FAILED_EVENT_TYPE = (
    "source_run_telegram_notification_failed"
)

SKIPPED_EVENT_TYPE = (
    "source_run_telegram_notification_skipped"
)

TEST_EVENT_TYPE = (
    "source_run_telegram_test_sent"
)


def get_notification_settings() -> dict[str, Any]:
    saved = get_setting(
        SETTING_KEY,
        {},
    ) or {}

    if not isinstance(saved, dict):
        return {"enabled": False}
    settings = dict(saved)
    if not bool(settings.get("enabled")):
        settings["enabled"] = False
        return settings

    required = {
        "apply_to_enabled_sources_only",
        "empty_result_alerts",
        "error_alerts",
        "configuration_warning_alerts",
        "cadence_skip_alerts",
        "disabled_source_alerts",
        "include_filter_reasons",
        "cooldown_minutes",
        "notification_style",
    }
    missing = sorted(required - set(settings))
    if missing:
        raise RuntimeError(
            "Canonical source_run_notifications is incomplete: " + ", ".join(missing)
        )

    try:
        cooldown = int(
            settings["cooldown_minutes"]
        )
    except (
        TypeError,
        ValueError,
    ):
        raise RuntimeError(
            "Canonical source-run notification cooldown is invalid."
        ) from None

    settings["cooldown_minutes"] = max(
        0,
        min(cooldown, 10080),
    )

    return settings


def enabled_source_names() -> list[str]:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT source_name
            FROM source_health
            WHERE enabled = 1
            ORDER BY
                source_tier,
                source_name
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        str(row["source_name"])
        for row in rows
    ]


def get_source_state(
    source_name: str,
) -> dict[str, Any] | None:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                source_name,
                source_tier,
                enabled,
                cadence_minutes,
                cost_mode,
                health_status,
                last_run_at,
                jobs_found_last_run,
                last_error
            FROM source_health
            WHERE source_name = ?
            """,
            (source_name,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    return {
        key: row[key]
        for key in row.keys()
    }


def telegram_runtime_enabled() -> bool:
    runtime = get_setting(
        "runtime",
        {},
    ) or {}

    return bool(
        runtime.get(
            "telegram_enabled",
            False,
        )
    )


def as_int(
    value: Any,
    default: int = 0,
) -> int:
    try:
        if value is None:
            return default

        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return default


def first_count(
    payload: dict[str, Any],
    *keys: str,
) -> int:
    for key in keys:
        if key in payload:
            return as_int(
                payload.get(key),
                0,
            )

    return 0


def normalize_errors(
    payload: dict[str, Any],
) -> list[str]:
    output: list[str] = []

    collections = [
        payload.get("errors"),
        payload.get(
            "telegram_dispatch_errors"
        ),
    ]

    for collection in collections:
        if not collection:
            continue

        if not isinstance(
            collection,
            list,
        ):
            collection = [collection]

        for item in collection:
            if isinstance(item, dict):
                company = str(
                    item.get("company")
                    or item.get("source")
                    or ""
                ).strip()

                message = str(
                    item.get("error")
                    or item.get("message")
                    or item
                ).strip()

                if company:
                    output.append(
                        f"{company}: {message}"
                    )
                else:
                    output.append(message)
            else:
                text = str(item).strip()

                if text:
                    output.append(text)

    return output


def classify_source_run(
    payload: dict[str, Any],
) -> str:
    action = str(
        payload.get("worker_action")
        or "run"
    ).strip().lower()

    skip_reason = str(
        payload.get("skip_reason")
        or ""
    ).strip().lower()

    errors = normalize_errors(payload)

    failed_companies = first_count(
        payload,
        "failed_companies",
    )

    success = bool(
        payload.get(
            "success",
            True,
        )
    )

    if (
        not success
        or errors
        or failed_companies > 0
    ):
        return "error"

    if action == "skip":
        if skip_reason == "source_disabled":
            return "disabled"

        if skip_reason == "cadence_not_due":
            return "cadence_skip"

        if (
            skip_reason
            == "source_not_configured"
            or skip_reason.startswith(
                "no_enabled_"
            )
            or "configuration"
            in skip_reason
        ):
            return "configuration"

        return "configuration"

    jobs_inserted = first_count(
        payload,
        "jobs_inserted",
        "inserted_count",
    )

    telegram_messages = first_count(
        payload,
        "telegram_messages",
        "telegram_messages_sent",
    )

    if (
        jobs_inserted > 0
        or telegram_messages > 0
    ):
        return "new_jobs"

    return "empty"


def setting_allows_type(
    notification_type: str,
    settings: dict[str, Any],
) -> bool:
    key_map = {
        "empty": "empty_result_alerts",
        "error": "error_alerts",
        "configuration": (
            "configuration_warning_alerts"
        ),
        "cadence_skip": (
            "cadence_skip_alerts"
        ),
        "disabled": (
            "disabled_source_alerts"
        ),
    }

    setting_key = key_map.get(
        notification_type
    )

    if not setting_key:
        return False

    return bool(
        settings.get(
            setting_key,
            False,
        )
    )


def parse_database_timestamp(
    value: Any,
) -> datetime | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def latest_sent_at(
    *,
    source_name: str,
    notification_type: str,
) -> datetime | None:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                created_at,
                payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY id DESC
            LIMIT 1000
            """,
            (SENT_EVENT_TYPE,),
        ).fetchall()
    finally:
        connection.close()

    for row in rows:
        try:
            event_payload = json.loads(
                row["payload_json"]
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if (
            event_payload.get("source")
            != source_name
        ):
            continue

        if (
            event_payload.get(
                "notification_type"
            )
            != notification_type
        ):
            continue

        return parse_database_timestamp(
            row["created_at"]
        )

    return None


def cooldown_remaining_minutes(
    *,
    source_name: str,
    notification_type: str,
    cooldown_minutes: int,
) -> int:
    if cooldown_minutes <= 0:
        return 0

    previous = latest_sent_at(
        source_name=source_name,
        notification_type=(
            notification_type
        ),
    )

    if previous is None:
        return 0

    elapsed_seconds = (
        datetime.now(timezone.utc)
        - previous
    ).total_seconds()

    remaining_seconds = (
        cooldown_minutes * 60
        - elapsed_seconds
    )

    if remaining_seconds <= 0:
        return 0

    return max(
        1,
        int(
            (
                remaining_seconds
                + 59
            )
            // 60
        ),
    )


def record_event(
    *,
    event_type: str,
    event_status: str,
    payload: dict[str, Any],
) -> None:
    connection = get_connection()

    try:
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
                NULL,
                ?,
                'source_run_notifier',
                ?,
                ?
            )
            """,
            (
                event_type,
                event_status,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
    finally:
        connection.close()


def escaped(value: Any) -> str:
    return html.escape(
        str(value or ""),
        quote=False,
    )


def skip_reason_text(
    skip_reason: str,
) -> str:
    normalized = skip_reason.strip().lower()

    descriptions = {
        "source_disabled": (
            "The adapter is disabled in "
            "the Sources dashboard."
        ),
        "cadence_not_due": (
            "The adapter was checked, but its "
            "configured cadence is not due yet."
        ),
        "source_not_configured": (
            "The adapter has not been fully "
            "configured."
        ),
        "no_enabled_greenhouse_companies": (
            "Greenhouse is enabled, but no "
            "enabled company board is configured."
        ),
        "no_enabled_lever_companies": (
            "Lever is enabled, but no enabled "
            "company board is configured."
        ),
    }

    if normalized in descriptions:
        return descriptions[normalized]

    if normalized.startswith(
        "no_enabled_"
    ):
        return (
            "The adapter is enabled, but it "
            "has no enabled company or account "
            "configuration."
        )

    if normalized:
        return normalized.replace(
            "_",
            " ",
        ).capitalize() + "."

    return (
        "The adapter could not complete "
        "a normal discovery run."
    )


def empty_result_reason(
    payload: dict[str, Any],
) -> str:
    raw_jobs = first_count(
        payload,
        "raw_jobs_found",
        "raw_jobs_fetched",
    )

    role_excluded = first_count(
        payload,
        "excluded_by_role",
        "excluded_by_target_role",
    )

    location_excluded = first_count(
        payload,
        "excluded_by_location",
    )

    unique_ready = first_count(
        payload,
        "unique_jobs_ready",
        "eligible_unique_jobs",
    )

    database_duplicates = first_count(
        payload,
        "database_duplicates",
        "duplicate_count",
    )

    if raw_jobs == 0:
        return (
            "The adapter completed successfully "
            "but returned no current postings."
        )

    if (
        role_excluded > 0
        and role_excluded >= raw_jobs
    ):
        return (
            "Every fetched posting was outside "
            "your active target-role rules."
        )

    if (
        unique_ready > 0
        and database_duplicates
        >= unique_ready
    ):
        return (
            f"{database_duplicates} matching "
            "job(s) were already stored, so "
            "duplicate Telegram cards were "
            "not sent."
        )

    if (
        role_excluded > 0
        or location_excluded > 0
    ):
        return (
            "No new posting remained after "
            "your role, location, and duplicate "
            "filters were applied."
        )

    return (
        "The run completed successfully, "
        "but no new eligible job was added."
    )


def format_source_run_message(
    *,
    payload: dict[str, Any],
    notification_type: str,
    settings: dict[str, Any],
    test_mode: bool = False,
) -> str:
    source_name = escaped(
        payload.get("source")
        or "Unknown Adapter"
    )

    success = bool(
        payload.get(
            "success",
            True,
        )
    )

    errors = normalize_errors(payload)

    if test_mode:
        icon = "🧪"
        title = (
            "Universal Adapter Alert Test"
        )
    elif notification_type == "error":
        icon = "🚨"

        if success:
            title = (
                f"{source_name} Completed "
                "With Errors"
            )
        else:
            title = f"{source_name} Run Failed"
    elif notification_type == "configuration":
        icon = "⚠️"
        title = (
            f"{source_name} Could Not Run"
        )
    elif notification_type == "cadence_skip":
        icon = "⏱️"
        title = f"{source_name} Run Skipped"
    elif notification_type == "disabled":
        icon = "⏸️"
        title = f"{source_name} Is Disabled"
    else:
        icon = "🔎"
        title = (
            f"{source_name} Run Completed"
        )

    lines = [
        f"{icon} <b>{title}</b>",
        "",
    ]

    raw_jobs = first_count(
        payload,
        "raw_jobs_found",
        "raw_jobs_fetched",
    )

    role_excluded = first_count(
        payload,
        "excluded_by_role",
        "excluded_by_target_role",
    )

    location_excluded = first_count(
        payload,
        "excluded_by_location",
    )

    within_run_duplicates = first_count(
        payload,
        "duplicates_within_run",
    )

    unique_ready = first_count(
        payload,
        "unique_jobs_ready",
        "eligible_unique_jobs",
    )

    database_duplicates = first_count(
        payload,
        "database_duplicates",
        "duplicate_count",
    )

    jobs_inserted = first_count(
        payload,
        "jobs_inserted",
        "inserted_count",
    )

    telegram_messages = first_count(
        payload,
        "telegram_messages",
        "telegram_messages_sent",
    )

    if (
        payload.get("worker_action")
        == "run"
        or raw_jobs
        or role_excluded
        or location_excluded
        or unique_ready
    ):
        lines.extend(
            [
                (
                    "📥 Jobs fetched: "
                    f"<b>{raw_jobs}</b>"
                ),
                (
                    "🎯 Matching role/location "
                    "rules: "
                    f"<b>{unique_ready}</b>"
                ),
            ]
        )

        if bool(
            settings.get(
                "include_filter_reasons",
                True,
            )
        ):
            lines.extend(
                [
                    (
                        "🚫 Excluded by target "
                        "role: "
                        f"<b>{role_excluded}</b>"
                    ),
                    (
                        "📍 Excluded by location: "
                        f"<b>{location_excluded}</b>"
                    ),
                    (
                        "♻️ Duplicates within run: "
                        f"<b>{within_run_duplicates}</b>"
                    ),
                    (
                        "🗃 Already stored: "
                        f"<b>{database_duplicates}</b>"
                    ),
                ]
            )

        lines.extend(
            [
                (
                    "✅ New jobs added: "
                    f"<b>{jobs_inserted}</b>"
                ),
                (
                    "📨 Telegram job cards sent: "
                    f"<b>{telegram_messages}</b>"
                ),
            ]
        )

    enabled_companies = first_count(
        payload,
        "enabled_company_count",
    )

    successful_companies = first_count(
        payload,
        "successful_companies",
    )

    failed_companies = first_count(
        payload,
        "failed_companies",
    )

    if (
        enabled_companies
        or successful_companies
        or failed_companies
    ):
        lines.extend(
            [
                "",
                (
                    "🏢 Company boards enabled: "
                    f"<b>{enabled_companies}</b>"
                ),
                (
                    "🟢 Company boards successful: "
                    f"<b>{successful_companies}</b>"
                ),
                (
                    "🔴 Company boards failed: "
                    f"<b>{failed_companies}</b>"
                ),
            ]
        )

    lines.append("")

    if notification_type == "empty":
        lines.append(
            "ℹ️ "
            + escaped(
                empty_result_reason(
                    payload
                )
            )
        )

    elif notification_type in {
        "configuration",
        "cadence_skip",
        "disabled",
    }:
        lines.append(
            "ℹ️ "
            + escaped(
                skip_reason_text(
                    str(
                        payload.get(
                            "skip_reason"
                        )
                        or ""
                    )
                )
            )
        )

    elif notification_type == "error":
        if errors:
            lines.append(
                "Reported issue(s):"
            )

            for error in errors[:4]:
                lines.append(
                    "• "
                    + escaped(error)[:700]
                )
        else:
            lines.append(
                "The adapter reported an "
                "unsuccessful run."
            )

    if test_mode:
        lines.extend(
            [
                "",
                (
                    "This confirms the universal "
                    "notifier can send source-run "
                    "messages for every enabled "
                    "dashboard adapter."
                ),
            ]
        )

    lines.extend(
        [
            "",
            "🔒 n8n actions triggered: <b>0</b>",
        ]
    )

    return "\n".join(lines)[:4000]


def notify_source_run(
    payload: dict[str, Any],
    *,
    force: bool = False,
    test_mode: bool = False,
) -> dict[str, Any]:
    source_name = str(
        payload.get("source")
        or ""
    ).strip()

    notification_type = (
        classify_source_run(payload)
    )

    base_result: dict[str, Any] = {
        "source": source_name,
        "notification_type": (
            notification_type
        ),
        "attempted": False,
        "sent": False,
        "reason": None,
        "message_id": None,
        "cooldown_remaining_minutes": 0,
        "n8n_calls": 0,
    }

    if not source_name:
        base_result["reason"] = (
            "source_name_missing"
        )
        return base_result

    if notification_type == "new_jobs":
        base_result["reason"] = (
            "new_jobs_use_interactive_cards"
        )
        return base_result

    settings = get_notification_settings()

    if (
        not settings.get("enabled")
        and not force
    ):
        base_result["reason"] = (
            "notifications_disabled"
        )
        return base_result

    if not telegram_runtime_enabled():
        base_result["reason"] = (
            "telegram_runtime_disabled"
        )
        return base_result

    if not CHAT_ID:
        base_result["reason"] = (
            "telegram_chat_id_missing"
        )
        return base_result

    source_state = get_source_state(
        source_name
    )

    if (
        settings.get(
            "apply_to_enabled_sources_only",
            True,
        )
        and not test_mode
    ):
        if source_state is None:
            base_result["reason"] = (
                "source_not_registered"
            )
            return base_result

        if not bool(
            source_state.get("enabled")
        ):
            base_result["reason"] = (
                "source_not_enabled"
            )
            return base_result

    if (
        not setting_allows_type(
            notification_type,
            settings,
        )
        and not force
    ):
        base_result["reason"] = (
            "notification_type_disabled"
        )
        return base_result

    cooldown = int(
        settings.get(
            "cooldown_minutes",
            360,
        )
    )

    remaining = (
        cooldown_remaining_minutes(
            source_name=source_name,
            notification_type=(
                notification_type
            ),
            cooldown_minutes=cooldown,
        )
    )

    if (
        remaining > 0
        and not force
        and not test_mode
    ):
        base_result[
            "cooldown_remaining_minutes"
        ] = remaining

        base_result["reason"] = (
            "cooldown_active"
        )

        record_event(
            event_type=SKIPPED_EVENT_TYPE,
            event_status="cooldown",
            payload={
                "source": source_name,
                "notification_type": (
                    notification_type
                ),
                "cooldown_remaining_minutes": (
                    remaining
                ),
            },
        )

        return base_result

    message = format_source_run_message(
        payload=payload,
        notification_type=(
            notification_type
        ),
        settings=settings,
        test_mode=test_mode,
    )

    base_result["attempted"] = True

    try:
        response = telegram_request(
            "sendMessage",
            {
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": (
                    "true"
                ),
            },
        )

        message_id = int(
            response["result"][
                "message_id"
            ]
        )

        event_type = (
            TEST_EVENT_TYPE
            if test_mode
            else SENT_EVENT_TYPE
        )

        record_event(
            event_type=event_type,
            event_status="completed",
            payload={
                "source": source_name,
                "notification_type": (
                    notification_type
                ),
                "message_id": message_id,
                "chat_id": CHAT_ID,
                "test_mode": test_mode,
                "summary": {
                    "raw_jobs_found": (
                        first_count(
                            payload,
                            "raw_jobs_found",
                            "raw_jobs_fetched",
                        )
                    ),
                    "jobs_inserted": (
                        first_count(
                            payload,
                            "jobs_inserted",
                            "inserted_count",
                        )
                    ),
                    "database_duplicates": (
                        first_count(
                            payload,
                            "database_duplicates",
                            "duplicate_count",
                        )
                    ),
                },
            },
        )

        base_result.update(
            {
                "sent": True,
                "reason": "sent",
                "message_id": message_id,
            }
        )

        return base_result

    except Exception as error:
        error_text = str(error)

        base_result["reason"] = (
            "telegram_send_failed"
        )

        base_result["error"] = error_text

        try:
            record_event(
                event_type=FAILED_EVENT_TYPE,
                event_status="failed",
                payload={
                    "source": source_name,
                    "notification_type": (
                        notification_type
                    ),
                    "error": error_text,
                    "test_mode": test_mode,
                },
            )
        except Exception:
            pass

        return base_result


def emit_source_run_result(
    payload: dict[str, Any],
) -> dict[str, Any]:
    output = dict(payload)

    # Provider output is not the notification authority. The shared targeting
    # gate commits source_runs first; this compatibility hook can then queue the
    # same stable logical identity without ever producing a duplicate card.
    try:
        from app.telegram_run_visibility import enqueue_committed_payload

        output["source_run_notification"] = enqueue_committed_payload(output)
    except Exception as error:
        output["source_run_notification"] = {
            "queued": False,
            "reason": f"durable_enqueue_deferred:{type(error).__name__}",
        }

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )

    return output


def run_guarded_main(
    source_name: str,
    main_function: Callable[[], None],
) -> None:
    try:
        main_function()

    except SystemExit:
        raise

    except Exception as error:
        emit_source_run_result(
            {
                "success": False,
                "mode": (
                    "guarded-adapter-failure"
                ),
                "source": source_name,
                "worker_action": "run",
                "network_request_made": None,
                "raw_jobs_found": 0,
                "jobs_inserted": 0,
                "telegram_messages": 0,
                "n8n_calls": 0,
                "errors": [
                    {
                        "error": str(error),
                    }
                ],
            }
        )

        raise


def run_self_test() -> dict[str, Any]:
    cases = {
        "empty": {
            "success": True,
            "source": "Self Test",
            "worker_action": "run",
            "raw_jobs_found": 12,
            "excluded_by_role": 8,
            "excluded_by_location": 2,
            "unique_jobs_ready": 2,
            "database_duplicates": 2,
            "jobs_inserted": 0,
            "telegram_messages": 0,
            "errors": [],
        },
        "error": {
            "success": False,
            "source": "Self Test",
            "worker_action": "run",
            "errors": [
                {
                    "error": "timeout",
                }
            ],
        },
        "configuration": {
            "success": True,
            "source": "Self Test",
            "worker_action": "skip",
            "skip_reason": (
                "no_enabled_test_companies"
            ),
        },
        "new_jobs": {
            "success": True,
            "source": "Self Test",
            "worker_action": "run",
            "jobs_inserted": 1,
            "telegram_messages": 1,
        },
    }

    classifications = {
        name: classify_source_run(
            payload
        )
        for name, payload in cases.items()
    }

    assert classifications == {
        "empty": "empty",
        "error": "error",
        "configuration": "configuration",
        "new_jobs": "new_jobs",
    }

    return {
        "success": True,
        "network_request_made": False,
        "telegram_messages": 0,
        "database_writes": 0,
        "n8n_calls": 0,
        "classifications": classifications,
        "enabled_sources_detected": (
            enabled_source_names()
        ),
    }


def send_test_notification(
    source_name: str,
) -> dict[str, Any]:
    payload = {
        "success": True,
        "mode": (
            "universal-source-notifier-test"
        ),
        "source": source_name,
        "worker_action": "run",
        "run_trigger": "manual_test",
        "network_request_made": False,
        "raw_jobs_found": 17,
        "excluded_by_role": 10,
        "excluded_by_location": 5,
        "duplicates_within_run": 0,
        "unique_jobs_ready": 2,
        "jobs_inserted": 0,
        "database_duplicates": 2,
        "telegram_messages": 0,
        "telegram_dispatch_errors": [],
        "n8n_calls": 0,
        "errors": [],
    }

    return notify_source_run(
        payload,
        force=True,
        test_mode=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--test-source",
        type=str,
        default=None,
    )

    args = parser.parse_args()

    if args.self_test:
        print(
            json.dumps(
                run_self_test(),
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if args.test_source:
        result = send_test_notification(
            args.test_source
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        if not result.get("sent"):
            raise SystemExit(1)

        return

    parser.print_help()


if __name__ == "__main__":
    main()

# AADIL_SOURCE_SUMMARY_CONTROLS_V1
_aadil_original_emit_source_run_result = emit_source_run_result


def emit_source_run_result(
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = _aadil_original_emit_source_run_result(payload)

    try:
        from app.telegram_control_center import post_source_run_controls

        controls = post_source_run_controls(
            payload=payload,
            notifier_result=result,
        )
    except Exception as error:
        controls = {
            "attached": False,
            "reason": "control_attachment_failed",
            "error": str(error),
        }

    if isinstance(result, dict):
        result["telegram_control_center"] = controls

    return result
