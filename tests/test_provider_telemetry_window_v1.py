from __future__ import annotations

import sqlite3
from pathlib import Path

from app import provider_telemetry_window_v1 as telemetry


def test_provider_window_metrics_sum_raw_source_throughput(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "telemetry.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            """CREATE TABLE source_runs(
                started_at TEXT,
                completed_at TEXT,
                raw_count INTEGER,
                normalized_count INTEGER,
                eligible_count INTEGER,
                new_eligible_count INTEGER
            )"""
        )
        connection.execute(
            "INSERT INTO source_runs VALUES(datetime('now','-30 minutes'),datetime('now','-20 minutes'),50000,49000,900,400)"
        )
        connection.execute(
            "INSERT INTO source_runs VALUES(datetime('now','-10 hours'),datetime('now','-9 hours'),15000,14500,300,120)"
        )
        connection.execute(
            "INSERT INTO source_runs VALUES(datetime('now','-48 hours'),datetime('now','-47 hours'),25000,24000,450,175)"
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(telemetry, "get_connection", lambda: sqlite3.connect(db))

    one_hour = telemetry.provider_window_metrics(1)
    assert one_hour == {
        "runs": 1,
        "fetched": 50000,
        "normalized": 49000,
        "eligible": 900,
        "new_eligible": 400,
    }

    day = telemetry.provider_window_metrics(24)
    assert day == {
        "runs": 2,
        "fetched": 65000,
        "normalized": 63500,
        "eligible": 1200,
        "new_eligible": 520,
    }

    all_time = telemetry.provider_window_metrics(0)
    assert all_time["fetched"] == 90000
    assert all_time["runs"] == 3


def test_provider_window_contract_is_raw_not_canonical() -> None:
    root = Path(__file__).resolve().parent.parent
    source = (root / "app" / "provider_telemetry_window_v1.py").read_text(encoding="utf-8")
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")

    assert "SUM(raw_count)" in source
    assert '"Jobs fetched"' in source
    assert '"Past 24 hours": 24' in source
    assert "source_runs" in source
    assert "COUNT(*) FROM jobs" not in source
    assert "install_provider_telemetry_window(product_pages)" in shell
    assert shell.index("install_career_os_quality_patch(product_pages)") < shell.index(
        "install_provider_telemetry_window(product_pages)"
    )
