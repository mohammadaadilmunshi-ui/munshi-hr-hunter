from __future__ import annotations

from dataclasses import dataclass

import pycountry


@dataclass(frozen=True)
class CountryOption:
    code: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.code})"


@dataclass(frozen=True)
class SubdivisionOption:
    code: str
    country_code: str
    name: str
    subdivision_type: str
    parent_code: str | None

    @property
    def short_code(self) -> str:
        prefix = f"{self.country_code}-"

        if self.code.startswith(prefix):
            return self.code[len(prefix):]

        return self.code

    @property
    def label(self) -> str:
        type_text = (
            f" · {self.subdivision_type}"
            if self.subdivision_type
            else ""
        )

        return (
            f"{self.name} "
            f"({self.short_code})"
            f"{type_text}"
        )


def get_countries() -> list[CountryOption]:
    countries = [
        CountryOption(
            code=country.alpha_2,
            name=country.name,
        )
        for country in pycountry.countries
    ]

    return sorted(
        countries,
        key=lambda country: country.name.casefold(),
    )


def get_country(
    country_code: str,
) -> CountryOption | None:
    country = pycountry.countries.get(
        alpha_2=str(country_code or "").upper()
    )

    if country is None:
        return None

    return CountryOption(
        code=country.alpha_2,
        name=country.name,
    )


def get_subdivisions(
    country_code: str,
) -> list[SubdivisionOption]:
    normalized_code = str(
        country_code or ""
    ).strip().upper()

    subdivisions: list[SubdivisionOption] = []

    for subdivision in pycountry.subdivisions:
        if subdivision.country_code != normalized_code:
            continue

        subdivisions.append(
            SubdivisionOption(
                code=subdivision.code,
                country_code=subdivision.country_code,
                name=subdivision.name,
                subdivision_type=(
                    getattr(
                        subdivision,
                        "type",
                        "",
                    )
                    or ""
                ),
                parent_code=getattr(
                    subdivision,
                    "parent_code",
                    None,
                ),
            )
        )

    return sorted(
        subdivisions,
        key=lambda subdivision: (
            subdivision.name.casefold(),
            subdivision.code,
        ),
    )
