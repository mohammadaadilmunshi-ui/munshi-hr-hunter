from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.database import (
    get_connection,
    get_setting,
)


COUNTRY_NAMES = {
    "US": "United States",
    "USA": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
}


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def normalize_country(value: Any) -> str:
    country = str(value or "US").strip().upper()

    aliases = {
        "USA": "US",
        "UNITED STATES": "US",
        "UK": "GB",
        "UNITED KINGDOM": "GB",
    }

    return aliases.get(country, country)


def country_search_name(country_code: str) -> str:
    return COUNTRY_NAMES.get(
        normalize_country(country_code),
        country_code,
    )


@dataclass(frozen=True)
class LocationRule:
    id: int
    location_name: str
    location_type: str
    city: str | None
    state: str | None
    country: str
    remote_allowed: bool
    hybrid_allowed: bool
    onsite_allowed: bool
    hybrid_max_miles: int | None
    priority_weight: int
    notes: str | None
    is_active: bool
    rule_purpose: str = "preference"

    @property
    def remote_only(self) -> bool:
        return (
            self.remote_allowed
            and not self.hybrid_allowed
            and not self.onsite_allowed
        )

    @property
    def search_location(self) -> str:
        if self.remote_only:
            return country_search_name(self.country)

        if self.city and self.state:
            return f"{self.city}, {self.state}"

        if self.city:
            return (
                f"{self.city}, "
                f"{country_search_name(self.country)}"
            )

        if self.state:
            return self.location_name or self.state

        if self.location_name:
            return self.location_name

        return country_search_name(self.country)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["search_location"] = self.search_location
        result["remote_only"] = self.remote_only
        return result


def load_active_location_rules() -> list[LocationRule]:
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                location_name,
                location_type,
                city,
                state,
                country,
                remote_allowed,
                hybrid_allowed,
                onsite_allowed,
                hybrid_max_miles,
                priority_weight,
                notes,
                is_active,
                rule_purpose
            FROM location_rules
            WHERE is_active = 1
            ORDER BY
                priority_weight DESC,
                id ASC
            """
        ).fetchall()
    finally:
        connection.close()

    return [
        LocationRule(
            id=int(row["id"]),
            location_name=str(
                row["location_name"] or ""
            ),
            location_type=str(
                row["location_type"] or ""
            ),
            city=(
                str(row["city"]).strip()
                if row["city"]
                else None
            ),
            state=(
                str(row["state"]).strip().upper()
                if row["state"]
                else None
            ),
            country=normalize_country(
                row["country"]
            ),
            remote_allowed=bool(
                row["remote_allowed"]
            ),
            hybrid_allowed=bool(
                row["hybrid_allowed"]
            ),
            onsite_allowed=bool(
                row["onsite_allowed"]
            ),
            hybrid_max_miles=(
                int(row["hybrid_max_miles"])
                if row["hybrid_max_miles"]
                is not None
                else None
            ),
            priority_weight=int(
                row["priority_weight"] or 0
            ),
            notes=(
                str(row["notes"]).strip()
                if row["notes"]
                else None
            ),
            is_active=bool(row["is_active"]),
            rule_purpose=str(row["rule_purpose"] or "preference"),
        )
        for row in rows
    ]


def load_target_roles() -> list[str]:
    targeting = get_setting(
        "targeting",
        {},
    ) or {}

    roles = targeting.get(
        "target_roles",
        [],
    )

    return [
        str(role).strip()
        for role in roles
        if str(role).strip()
    ]


def build_search_term(
    target_roles: list[str],
) -> str:
    safe_roles = []

    for role in target_roles:
        cleaned = role.replace('"', "").strip()

        if cleaned:
            safe_roles.append(f'"{cleaned}"')

    return " OR ".join(safe_roles)


def load_source_configuration(
    source_name: str,
) -> dict[str, Any] | None:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM source_health
            WHERE lower(source_name) = lower(?)
            """,
            (source_name,),
        ).fetchone()
    finally:
        connection.close()

    return dict(row) if row else None


def build_location_search_plan() -> list[dict[str, Any]]:
    rules = load_active_location_rules()

    plans: list[dict[str, Any]] = []
    seen: set[tuple[str, bool]] = set()

    ordered_rules = sorted(
        rules,
        key=lambda rule: (
            0 if rule.rule_purpose.casefold() == "eligibility" else 1,
            -rule.priority_weight,
            rule.id,
        ),
    )

    for rule in ordered_rules:
        key = (
            normalize_text(rule.search_location),
            rule.remote_only,
        )

        if key in seen:
            continue

        seen.add(key)

        plans.append(
            {
                "rule_id": rule.id,
                "rule_name": rule.location_name,
                "rule_type": rule.location_type,
                "search_location": (
                    rule.search_location
                ),
                "remote_only": rule.remote_only,
                "remote_allowed": (
                    rule.remote_allowed
                ),
                "hybrid_allowed": (
                    rule.hybrid_allowed
                ),
                "onsite_allowed": (
                    rule.onsite_allowed
                ),
                "hybrid_max_miles": (
                    rule.hybrid_max_miles
                ),
                "priority_weight": (
                    rule.priority_weight
                ),
                "country": rule.country,
                "state": rule.state,
                "city": rule.city,
                "rule_purpose": rule.rule_purpose,
            }
        )

    return plans
