from __future__ import annotations

import html
import re
from typing import Any


US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}


def clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    return text


def html_to_text(value: Any) -> str | None:
    text = clean_text(value)

    if not text:
        return None

    decoded = html.unescape(text)

    decoded = re.sub(
        r"<\s*br\s*/?\s*>",
        "\n",
        decoded,
        flags=re.IGNORECASE,
    )

    decoded = re.sub(
        r"</\s*(p|div|li|h[1-6])\s*>",
        "\n",
        decoded,
        flags=re.IGNORECASE,
    )

    decoded = re.sub(
        r"<[^>]+>",
        " ",
        decoded,
    )

    decoded = html.unescape(decoded)

    decoded = re.sub(
        r"[ \t]+",
        " ",
        decoded,
    )

    decoded = re.sub(
        r"\n\s*\n+",
        "\n\n",
        decoded,
    )

    return decoded.strip() or None


def normalize_country(value: Any) -> str:
    text = str(value or "").strip().upper()

    aliases = {
        "USA": "US",
        "UNITED STATES": "US",
        "UNITED STATES OF AMERICA": "US",
    }

    return aliases.get(text, text)


def parse_location(
    value: Any,
) -> dict[str, str | None]:
    raw = clean_text(value)

    if not raw:
        return {
            "location_raw": None,
            "city": None,
            "state": None,
            "country": None,
        }

    parts = [
        part.strip()
        for part in raw.split(",")
        if part.strip()
    ]

    city: str | None = None
    state: str | None = None
    country: str | None = None

    if parts:
        final_part = parts[-1].upper()

        if final_part in {
            "US",
            "USA",
            "UNITED STATES",
            "UNITED STATES OF AMERICA",
        }:
            country = "US"
            parts = parts[:-1]

    if parts:
        candidate_state = parts[-1].upper()

        if candidate_state in US_STATE_CODES:
            state = candidate_state
            parts = parts[:-1]

    if parts:
        city = ", ".join(parts)

    return {
        "location_raw": raw,
        "city": city,
        "state": state,
        "country": normalize_country(country) or None,
    }


def infer_remote_type(
    location: Any,
    description: Any,
) -> str:
    combined = " ".join(
        str(value or "")
        for value in (
            location,
            description,
        )
    ).lower()

    if "hybrid" in combined:
        return "Hybrid"

    if re.search(
        r"\b(remote|work from home|wfh)\b",
        combined,
    ):
        return "Remote"

    return "Onsite"
