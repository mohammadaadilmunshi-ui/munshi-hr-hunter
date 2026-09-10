from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from app import database


ROOT = Path(__file__).resolve().parents[1]
migration = importlib.import_module(
    "migrations.052_adapter_runtime_foundation_v1"
)


def _staging_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_SHADOW_MODE", "true")
    for key in (
        "PRODUCTION_STATE_IMPORTED",
        "PRODUCTION_CALLBACKS_ENABLED",
        "HUNTER_ENABLE_TELEGRAM",
        "HUNTER_ENABLE_DISCOVERY_SCHEDULER",
        "HUNTER_ENABLE_COORDINATOR",
    ):
        monkeypatch.setenv(key, "false")


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(database.SCHEMA_SQL)
    database.ensure_job_detail_columns(connection)
    database.ensure_operational_columns(connection)
    return connection


def test_source_catalog_matches_observed_control_plane_shape() -> None:
    payload = json.loads(
        (ROOT / "config" / "source_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    sources = payload["sources"]

    assert len(sources) == 32
    assert len(
        {
            str(item["source_name"]).casefold()
            for item in sources
        }
    ) == 32
    assert all(
        item["staging_default_enabled"] is False
        for item in sources
    )

    names = {item["source_name"] for item in sources}
    for required in (
        "Greenhouse",
        "Ashby",
        "Lever",
        "SmartRecruiters",
        "Workable",
        "LinkedIn Jobs (JobSpy)",
        "Indeed Jobs (JobSpy)",
        "JobSpy",
        "Workday",
        "BambooHR",
    ):
        assert required in names


def test_foundation_bootstraps_control_plane_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _staging_env(monkeypatch)
    connection = _connection()
    try:
        result = migration.apply(connection)

        assert result["source_count"] == 32
        assert result["adapter_count"] == 30
        assert result["provider_board_count"] == 2

        source_count = connection.execute(
            "SELECT COUNT(*) FROM source_health"
        ).fetchone()[0]
        enabled_count = connection.execute(
            "SELECT COUNT(*) FROM source_health WHERE enabled=1"
        ).fetchone()[0]
        assert source_count == 32
        assert enabled_count == 0

        schedule_count = connection.execute(
            "SELECT COUNT(*) FROM source_random_schedule"
        ).fetchone()[0]
        non_disabled = connection.execute(
            """
            SELECT COUNT(*)
            FROM source_random_schedule
            WHERE schedule_state!='disabled'
            """
        ).fetchone()[0]
        assert schedule_count == 32
        assert non_disabled == 0

        adapter_count = connection.execute(
            "SELECT COUNT(*) FROM adapter_coverage"
        ).fetchone()[0]
        assert adapter_count == 30

        provider_boards = connection.execute(
            "SELECT COUNT(*) FROM provider_board_registry"
        ).fetchone()[0]
        provider_boards_enabled = connection.execute(
            """
            SELECT COUNT(*)
            FROM provider_board_registry
            WHERE enabled=1
            """
        ).fetchone()[0]
        assert provider_boards == 2
        assert provider_boards_enabled == 0

        table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name='ats_company_registry'
            """
        ).fetchone()
        assert table is not None

        ats_rows = connection.execute(
            "SELECT COUNT(*) FROM ats_company_registry"
        ).fetchone()[0]
        assert ats_rows == 0

        for key in (
            "source_worker_registry",
            "provider_runtime",
            "downstream_contract",
            "adapter_catalog",
            "query_strategy",
            "orchestration",
            "verified_provider_boards",
            "targeting",
            "authorization",
        ):
            assert connection.execute(
                """
                SELECT 1
                FROM settings
                WHERE setting_key=?
                """,
                (key,),
            ).fetchone()

        worker_registry = json.loads(
            connection.execute(
                """
                SELECT value_json
                FROM settings
                WHERE setting_key='source_worker_registry'
                """
            ).fetchone()[0]
        )
        assert len(worker_registry["workers"]) == 24

        receipt = connection.execute(
            """
            SELECT detail_json
            FROM schema_migrations
            WHERE migration_id='052_adapter_runtime_foundation_v1'
            """
        ).fetchone()
        assert receipt is not None
        detail = json.loads(receipt[0])
        assert detail["network_requests"] == 0
        assert detail["jobs_stored"] == 0
        assert detail["all_new_sources_default_disabled"] is True

        assert connection.execute(
            "PRAGMA quick_check"
        ).fetchone()[0] == "ok"
    finally:
        connection.close()


def test_foundation_preserves_explicit_later_source_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _staging_env(monkeypatch)
    connection = _connection()
    try:
        migration.apply(connection)
        connection.execute(
            """
            UPDATE source_health
            SET enabled=1
            WHERE source_name='Workable'
            """
        )
        connection.execute(
            """
            UPDATE source_random_schedule
            SET schedule_state='cooldown',
                schedule_reason='manual_provider_probe'
            WHERE source_name='Workable'
            """
        )

        migration.apply(connection)

        provider_boards = connection.execute(
            "SELECT COUNT(*) FROM provider_board_registry"
        ).fetchone()[0]
        assert provider_boards == 2

        enabled = connection.execute(
            """
            SELECT enabled
            FROM source_health
            WHERE source_name='Workable'
            """
        ).fetchone()[0]
        schedule = connection.execute(
            """
            SELECT schedule_state,schedule_reason
            FROM source_random_schedule
            WHERE source_name='Workable'
            """
        ).fetchone()

        assert enabled == 1
        assert schedule["schedule_state"] == "cooldown"
        assert schedule["schedule_reason"] == "manual_provider_probe"
    finally:
        connection.close()


def test_foundation_refuses_writer_or_production_like_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _staging_env(monkeypatch)
    monkeypatch.setenv(
        "HUNTER_ENABLE_DISCOVERY_SCHEDULER",
        "true",
    )
    connection = _connection()
    try:
        with pytest.raises(RuntimeError, match="refuses active"):
            migration.apply(connection)
    finally:
        connection.close()
