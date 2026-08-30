from __future__ import annotations

import json

from app.discovery_config import (
    build_location_search_plan,
    build_search_term,
    load_active_location_rules,
    load_source_configuration,
    load_target_roles,
)


def main() -> None:
    location_rules = load_active_location_rules()
    target_roles = load_target_roles()
    source = load_source_configuration("JobSpy")
    search_plan = build_location_search_plan()

    result = {
        "success": True,
        "configuration_source": "SQLite dashboard",
        "location_values_hardcoded": False,
        "network_request_made": False,
        "database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "jobspy_source": source,
        "target_roles": target_roles,
        "generated_search_term": build_search_term(
            target_roles
        ),
        "active_location_rule_count": len(
            location_rules
        ),
        "active_location_rules": [
            rule.to_dict()
            for rule in location_rules
        ],
        "generated_search_plan": search_plan,
    }

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
