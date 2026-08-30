from __future__ import annotations

from app import source_cooldown


def test_scheduler_policy_comes_from_canonical_settings(monkeypatch) -> None:
    settings = {
        "provider_runtime": {
            "source_schedule": {
                "default_minimum_cadence_minutes": 17,
                "minimum_cadence_minutes_by_source": {"Example Source": 43},
                "jitter_floor_minutes": 2,
                "jitter_ceiling_minutes": 11,
                "jitter_min_ratio": 0.1,
                "jitter_max_ratio": 0.2,
            }
        },
        "source_worker_registry": {"retired_source_keys": ["Retired Source"]},
    }
    monkeypatch.setattr(
        source_cooldown,
        "get_setting",
        lambda key, default=None: settings.get(key, default),
    )

    assert source_cooldown.get_effective_cadence_minutes(
        "Example Source", row={"source_name": "Example Source", "cadence_minutes": 5}
    ) == 43
    assert source_cooldown.get_effective_cadence_minutes(
        "Unlisted Source", row={"source_name": "Unlisted Source", "cadence_minutes": 5}
    ) == 17
    assert "retiredsource" in source_cooldown._retired_source_keys()
    assert source_cooldown._jitter_range(50) == (5, 10)
