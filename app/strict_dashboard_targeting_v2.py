from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from typing import Any, Iterable

MARKER = "AADIL_OPT_US_DASHBOARD_TARGETING_ENFORCEMENT_V2"

STATIC_COUNTRIES: dict[str, list[str]] = {"AD": ["AD", "AND", "Andorra", "Principality of Andorra"], "AE": ["AE", "ARE", "United Arab Emirates"], "AF": ["AF", "AFG", "Afghanistan", "Islamic Republic of Afghanistan"], "AG": ["AG", "ATG", "Antigua and Barbuda"], "AI": ["AI", "AIA", "Anguilla"], "AL": ["AL", "ALB", "Albania", "Republic of Albania"], "AM": ["AM", "ARM", "Armenia", "Republic of Armenia"], "AO": ["AGO", "AO", "Angola", "Republic of Angola"], "AQ": ["AQ", "ATA", "Antarctica"], "AR": ["AR", "ARG", "Argentina", "Argentine Republic"], "AS": ["AS", "ASM", "American Samoa"], "AT": ["AT", "AUT", "Austria", "Republic of Austria"], "AU": ["AU", "AUS", "Australia"], "AW": ["ABW", "AW", "Aruba"], "AX": ["ALA", "AX", "Åland Islands"], "AZ": ["AZ", "AZE", "Azerbaijan", "Republic of Azerbaijan"], "BA": ["BA", "BIH", "Bosnia and Herzegovina", "Republic of Bosnia and Herzegovina"], "BB": ["BB", "BRB", "Barbados"], "BD": ["BD", "BGD", "Bangladesh", "People's Republic of Bangladesh"], "BE": ["BE", "BEL", "Belgium", "Kingdom of Belgium"], "BF": ["BF", "BFA", "Burkina Faso"], "BG": ["BG", "BGR", "Bulgaria", "Republic of Bulgaria"], "BH": ["BH", "BHR", "Bahrain", "Kingdom of Bahrain"], "BI": ["BDI", "BI", "Burundi", "Republic of Burundi"], "BJ": ["BEN", "BJ", "Benin", "Republic of Benin"], "BL": ["BL", "BLM", "Saint Barthélemy"], "BM": ["BM", "BMU", "Bermuda"], "BN": ["BN", "BRN", "Brunei", "Brunei Darussalam"], "BO": ["BO", "BOL", "Bolivia", "Bolivia, Plurinational State of", "Plurinational State of Bolivia"], "BQ": ["BES", "BQ", "Bonaire, Sint Eustatius and Saba"], "BR": ["BR", "BRA", "Brazil", "Federative Republic of Brazil"], "BS": ["BHS", "BS", "Bahamas", "Commonwealth of the Bahamas"], "BT": ["BT", "BTN", "Bhutan", "Kingdom of Bhutan"], "BV": ["BV", "BVT", "Bouvet Island"], "BW": ["BW", "BWA", "Botswana", "Republic of Botswana"], "BY": ["BLR", "BY", "Belarus", "Republic of Belarus"], "BZ": ["BLZ", "BZ", "Belize"], "CA": ["CA", "CAN", "Canada"], "CC": ["CC", "CCK", "Cocos (Keeling) Islands"], "CD": ["CD", "COD", "Congo, The Democratic Republic of the"], "CF": ["CAF", "CF", "Central African Republic"], "CG": ["CG", "COG", "Congo", "Republic of the Congo"], "CH": ["CH", "CHE", "Swiss Confederation", "Switzerland"], "CI": ["CI", "CIV", "Côte d'Ivoire", "Republic of Côte d'Ivoire"], "CK": ["CK", "COK", "Cook Islands"], "CL": ["CHL", "CL", "Chile", "Republic of Chile"], "CM": ["CM", "CMR", "Cameroon", "Republic of Cameroon"], "CN": ["CHN", "CN", "China", "People's Republic of China"], "CO": ["CO", "COL", "Colombia", "Republic of Colombia"], "CR": ["CR", "CRI", "Costa Rica", "Republic of Costa Rica"], "CU": ["CU", "CUB", "Cuba", "Republic of Cuba"], "CV": ["CPV", "CV", "Cabo Verde", "Republic of Cabo Verde"], "CW": ["CUW", "CW", "Curaçao"], "CX": ["CX", "CXR", "Christmas Island"], "CY": ["CY", "CYP", "Cyprus", "Republic of Cyprus"], "CZ": ["CZ", "CZE", "Czech Republic", "Czechia"], "DE": ["DE", "DEU", "Federal Republic of Germany", "Germany"], "DJ": ["DJ", "DJI", "Djibouti", "Republic of Djibouti"], "DK": ["DK", "DNK", "Denmark", "Kingdom of Denmark"], "DM": ["Commonwealth of Dominica", "DM", "DMA", "Dominica"], "DO": ["DO", "DOM", "Dominican Republic"], "DZ": ["Algeria", "DZ", "DZA", "People's Democratic Republic of Algeria"], "EC": ["EC", "ECU", "Ecuador", "Republic of Ecuador"], "EE": ["EE", "EST", "Estonia", "Republic of Estonia"], "EG": ["Arab Republic of Egypt", "EG", "EGY", "Egypt"], "EH": ["EH", "ESH", "Western Sahara"], "ER": ["ER", "ERI", "Eritrea", "the State of Eritrea"], "ES": ["ES", "ESP", "Kingdom of Spain", "Spain"], "ET": ["ET", "ETH", "Ethiopia", "Federal Democratic Republic of Ethiopia"], "FI": ["FI", "FIN", "Finland", "Republic of Finland"], "FJ": ["FJ", "FJI", "Fiji", "Republic of Fiji"], "FK": ["FK", "FLK", "Falkland Islands (Malvinas)"], "FM": ["FM", "FSM", "Federated States of Micronesia", "Micronesia, Federated States of"], "FO": ["FO", "FRO", "Faroe Islands"], "FR": ["FR", "FRA", "France", "French Republic"], "GA": ["GA", "GAB", "Gabon", "Gabonese Republic"], "GB": ["Britain", "England", "GB", "GBR", "Great Britain", "U.K.", "UK", "United Kingdom", "United Kingdom of Great Britain and Northern Ireland"], "GD": ["GD", "GRD", "Grenada"], "GE": ["GE", "GEO", "Georgia"], "GF": ["French Guiana", "GF", "GUF"], "GG": ["GG", "GGY", "Guernsey"], "GH": ["GH", "GHA", "Ghana", "Republic of Ghana"], "GI": ["GI", "GIB", "Gibraltar"], "GL": ["GL", "GRL", "Greenland"], "GM": ["GM", "GMB", "Gambia", "Republic of the Gambia"], "GN": ["GIN", "GN", "Guinea", "Republic of Guinea"], "GP": ["GLP", "GP", "Guadeloupe"], "GQ": ["Equatorial Guinea", "GNQ", "GQ", "Republic of Equatorial Guinea"], "GR": ["GR", "GRC", "Greece", "Hellenic Republic"], "GS": ["GS", "SGS", "South Georgia and the South Sandwich Islands"], "GT": ["GT", "GTM", "Guatemala", "Republic of Guatemala"], "GU": ["GU", "GUM", "Guam"], "GW": ["GNB", "GW", "Guinea-Bissau", "Republic of Guinea-Bissau"], "GY": ["GUY", "GY", "Guyana", "Republic of Guyana"], "HK": ["HK", "HKG", "Hong Kong", "Hong Kong Special Administrative Region of China"], "HM": ["HM", "HMD", "Heard Island and McDonald Islands"], "HN": ["HN", "HND", "Honduras", "Republic of Honduras"], "HR": ["Croatia", "HR", "HRV", "Republic of Croatia"], "HT": ["HT", "HTI", "Haiti", "Republic of Haiti"], "HU": ["HU", "HUN", "Hungary"], "ID": ["ID", "IDN", "Indonesia", "Republic of Indonesia"], "IE": ["IE", "IRL", "Ireland"], "IL": ["IL", "ISR", "Israel", "State of Israel"], "IM": ["IM", "IMN", "Isle of Man"], "IN": ["IN", "IND", "India", "Republic of India"], "IO": ["British Indian Ocean Territory", "IO", "IOT"], "IQ": ["IQ", "IRQ", "Iraq", "Republic of Iraq"], "IR": ["IR", "IRN", "Iran", "Iran, Islamic Republic of", "Islamic Republic of Iran"], "IS": ["IS", "ISL", "Iceland", "Republic of Iceland"], "IT": ["IT", "ITA", "Italian Republic", "Italy"], "JE": ["JE", "JEY", "Jersey"], "JM": ["JAM", "JM", "Jamaica"], "JO": ["Hashemite Kingdom of Jordan", "JO", "JOR", "Jordan"], "JP": ["JP", "JPN", "Japan"], "KE": ["KE", "KEN", "Kenya", "Republic of Kenya"], "KG": ["KG", "KGZ", "Kyrgyz Republic", "Kyrgyzstan"], "KH": ["Cambodia", "KH", "KHM", "Kingdom of Cambodia"], "KI": ["KI", "KIR", "Kiribati", "Republic of Kiribati"], "KM": ["COM", "Comoros", "KM", "Union of the Comoros"], "KN": ["KN", "KNA", "Saint Kitts and Nevis"], "KP": ["Democratic People's Republic of Korea", "KP", "Korea, Democratic People's Republic of", "North Korea", "PRK"], "KR": ["KOR", "KR", "Korea, Republic of", "Republic of Korea", "South Korea"], "KW": ["KW", "KWT", "Kuwait", "State of Kuwait"], "KY": ["CYM", "Cayman Islands", "KY"], "KZ": ["KAZ", "KZ", "Kazakhstan", "Republic of Kazakhstan"], "LA": ["LA", "LAO", "Lao People's Democratic Republic", "Laos"], "LB": ["LB", "LBN", "Lebanese Republic", "Lebanon"], "LC": ["LC", "LCA", "Saint Lucia"], "LI": ["LI", "LIE", "Liechtenstein", "Principality of Liechtenstein"], "LK": ["Democratic Socialist Republic of Sri Lanka", "LK", "LKA", "Sri Lanka"], "LR": ["LBR", "LR", "Liberia", "Republic of Liberia"], "LS": ["Kingdom of Lesotho", "LS", "LSO", "Lesotho"], "LT": ["LT", "LTU", "Lithuania", "Republic of Lithuania"], "LU": ["Grand Duchy of Luxembourg", "LU", "LUX", "Luxembourg"], "LV": ["LV", "LVA", "Latvia", "Republic of Latvia"], "LY": ["LBY", "LY", "Libya"], "MA": ["Kingdom of Morocco", "MA", "MAR", "Morocco"], "MC": ["MC", "MCO", "Monaco", "Principality of Monaco"], "MD": ["MD", "MDA", "Moldova", "Moldova, Republic of", "Republic of Moldova"], "ME": ["ME", "MNE", "Montenegro"], "MF": ["MAF", "MF", "Saint Martin (French part)"], "MG": ["MDG", "MG", "Madagascar", "Republic of Madagascar"], "MH": ["MH", "MHL", "Marshall Islands", "Republic of the Marshall Islands"], "MK": ["MK", "MKD", "North Macedonia", "Republic of North Macedonia"], "ML": ["ML", "MLI", "Mali", "Republic of Mali"], "MM": ["MM", "MMR", "Myanmar", "Republic of Myanmar"], "MN": ["MN", "MNG", "Mongolia"], "MO": ["MAC", "MO", "Macao", "Macao Special Administrative Region of China"], "MP": ["Commonwealth of the Northern Mariana Islands", "MNP", "MP", "Northern Mariana Islands"], "MQ": ["MQ", "MTQ", "Martinique"], "MR": ["Islamic Republic of Mauritania", "MR", "MRT", "Mauritania"], "MS": ["MS", "MSR", "Montserrat"], "MT": ["MLT", "MT", "Malta", "Republic of Malta"], "MU": ["MU", "MUS", "Mauritius", "Republic of Mauritius"], "MV": ["MDV", "MV", "Maldives", "Republic of Maldives"], "MW": ["MW", "MWI", "Malawi", "Republic of Malawi"], "MX": ["MEX", "MX", "Mexico", "United Mexican States"], "MY": ["MY", "MYS", "Malaysia"], "MZ": ["MOZ", "MZ", "Mozambique", "Republic of Mozambique"], "NA": ["NA", "NAM", "Namibia", "Republic of Namibia"], "NC": ["NC", "NCL", "New Caledonia"], "NE": ["NE", "NER", "Niger", "Republic of the Niger"], "NF": ["NF", "NFK", "Norfolk Island"], "NG": ["Federal Republic of Nigeria", "NG", "NGA", "Nigeria"], "NI": ["NI", "NIC", "Nicaragua", "Republic of Nicaragua"], "NL": ["Kingdom of the Netherlands", "NL", "NLD", "Netherlands"], "NO": ["Kingdom of Norway", "NO", "NOR", "Norway"], "NP": ["Federal Democratic Republic of Nepal", "NP", "NPL", "Nepal"], "NR": ["NR", "NRU", "Nauru", "Republic of Nauru"], "NU": ["NIU", "NU", "Niue"], "NZ": ["NZ", "NZL", "New Zealand"], "OM": ["OM", "OMN", "Oman", "Sultanate of Oman"], "PA": ["PA", "PAN", "Panama", "Republic of Panama"], "PE": ["PE", "PER", "Peru", "Republic of Peru"], "PF": ["French Polynesia", "PF", "PYF"], "PG": ["Independent State of Papua New Guinea", "PG", "PNG", "Papua New Guinea"], "PH": ["PH", "PHL", "Philippines", "Republic of the Philippines"], "PK": ["Islamic Republic of Pakistan", "PAK", "PK", "Pakistan"], "PL": ["PL", "POL", "Poland", "Republic of Poland"], "PM": ["PM", "SPM", "Saint Pierre and Miquelon"], "PN": ["PCN", "PN", "Pitcairn"], "PR": ["PR", "PRI", "Puerto Rico"], "PS": ["PS", "PSE", "Palestine, State of", "the State of Palestine"], "PT": ["PRT", "PT", "Portugal", "Portuguese Republic"], "PW": ["PLW", "PW", "Palau", "Republic of Palau"], "PY": ["PRY", "PY", "Paraguay", "Republic of Paraguay"], "QA": ["QA", "QAT", "Qatar", "State of Qatar"], "RE": ["RE", "REU", "Réunion"], "RO": ["RO", "ROU", "Romania"], "RS": ["RS", "Republic of Serbia", "SRB", "Serbia"], "RU": ["RU", "RUS", "Russia", "Russian Federation"], "RW": ["RW", "RWA", "Rwanda", "Rwandese Republic"], "SA": ["Kingdom of Saudi Arabia", "SA", "SAU", "Saudi Arabia"], "SB": ["SB", "SLB", "Solomon Islands"], "SC": ["Republic of Seychelles", "SC", "SYC", "Seychelles"], "SD": ["Republic of the Sudan", "SD", "SDN", "Sudan"], "SE": ["Kingdom of Sweden", "SE", "SWE", "Sweden"], "SG": ["Republic of Singapore", "SG", "SGP", "Singapore"], "SH": ["SH", "SHN", "Saint Helena, Ascension and Tristan da Cunha"], "SI": ["Republic of Slovenia", "SI", "SVN", "Slovenia"], "SJ": ["SJ", "SJM", "Svalbard and Jan Mayen"], "SK": ["SK", "SVK", "Slovak Republic", "Slovakia"], "SL": ["Republic of Sierra Leone", "SL", "SLE", "Sierra Leone"], "SM": ["Republic of San Marino", "SM", "SMR", "San Marino"], "SN": ["Republic of Senegal", "SEN", "SN", "Senegal"], "SO": ["Federal Republic of Somalia", "SO", "SOM", "Somalia"], "SR": ["Republic of Suriname", "SR", "SUR", "Suriname"], "SS": ["Republic of South Sudan", "SS", "SSD", "South Sudan"], "ST": ["Democratic Republic of Sao Tome and Principe", "ST", "STP", "Sao Tome and Principe"], "SV": ["El Salvador", "Republic of El Salvador", "SLV", "SV"], "SX": ["SX", "SXM", "Sint Maarten (Dutch part)"], "SY": ["SY", "SYR", "Syria", "Syrian Arab Republic"], "SZ": ["Eswatini", "Kingdom of Eswatini", "SWZ", "SZ"], "TC": ["TC", "TCA", "Turks and Caicos Islands"], "TD": ["Chad", "Republic of Chad", "TCD", "TD"], "TF": ["ATF", "French Southern Territories", "TF"], "TG": ["TG", "TGO", "Togo", "Togolese Republic"], "TH": ["Kingdom of Thailand", "TH", "THA", "Thailand"], "TJ": ["Republic of Tajikistan", "TJ", "TJK", "Tajikistan"], "TK": ["TK", "TKL", "Tokelau"], "TL": ["Democratic Republic of Timor-Leste", "TL", "TLS", "Timor-Leste"], "TM": ["TKM", "TM", "Turkmenistan"], "TN": ["Republic of Tunisia", "TN", "TUN", "Tunisia"], "TO": ["Kingdom of Tonga", "TO", "TON", "Tonga"], "TR": ["Republic of Türkiye", "TR", "TUR", "Türkiye"], "TT": ["Republic of Trinidad and Tobago", "TT", "TTO", "Trinidad and Tobago"], "TV": ["TUV", "TV", "Tuvalu"], "TW": ["TW", "TWN", "Taiwan", "Taiwan, Province of China"], "TZ": ["TZ", "TZA", "Tanzania", "Tanzania, United Republic of", "United Republic of Tanzania"], "UA": ["UA", "UKR", "Ukraine"], "UG": ["Republic of Uganda", "UG", "UGA", "Uganda"], "UM": ["UM", "UMI", "United States Minor Outlying Islands"], "US": ["America", "U.S.", "U.S.A.", "US", "USA", "United States", "United States of America"], "UY": ["Eastern Republic of Uruguay", "URY", "UY", "Uruguay"], "UZ": ["Republic of Uzbekistan", "UZ", "UZB", "Uzbekistan"], "VA": ["Holy See (Vatican City State)", "VA", "VAT"], "VC": ["Saint Vincent and the Grenadines", "VC", "VCT"], "VE": ["Bolivarian Republic of Venezuela", "VE", "VEN", "Venezuela", "Venezuela, Bolivarian Republic of"], "VG": ["British Virgin Islands", "VG", "VGB", "Virgin Islands, British"], "VI": ["VI", "VIR", "Virgin Islands of the United States", "Virgin Islands, U.S."], "VN": ["Socialist Republic of Viet Nam", "VN", "VNM", "Viet Nam", "Vietnam"], "VU": ["Republic of Vanuatu", "VU", "VUT", "Vanuatu"], "WF": ["WF", "WLF", "Wallis and Futuna"], "WS": ["Independent State of Samoa", "Samoa", "WS", "WSM"], "YE": ["Republic of Yemen", "YE", "YEM", "Yemen"], "YT": ["MYT", "Mayotte", "YT"], "ZA": ["Republic of South Africa", "South Africa", "ZA", "ZAF"], "ZM": ["Republic of Zambia", "ZM", "ZMB", "Zambia"], "ZW": ["Republic of Zimbabwe", "ZW", "ZWE", "Zimbabwe"]}

US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
}

US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
    "puerto rico": "PR", "u s virgin islands": "VI", "guam": "GU",
    "american samoa": "AS", "northern mariana islands": "MP",
}

# Georgia can mean the U.S. state or the country; do not infer the foreign
# country from free-text location alone. An explicit country field still wins.
AMBIGUOUS_COUNTRY_NAMES = {"georgia"}

MANUAL_COUNTRY_ALIASES = {
    "us": "US", "u s": "US", "usa": "US", "u s a": "US",
    "united states": "US", "united states of america": "US",
    "america": "US", "uk": "GB", "u k": "GB",
    "great britain": "GB", "britain": "GB", "england": "GB",
    "russia": "RU", "south korea": "KR", "north korea": "KP",
    "czech republic": "CZ", "vietnam": "VN",
}

PROTECTED_STATUSES = {
    "approved_for_n8n", "sent_to_n8n", "application_ready", "n8n_failed",
    "already_applied", "hold", "held",
}

EXEMPT_PATH_TERMS = (
    "manual_input", "manual input", "telegram_manual", "telegram manual",
    "stored_job_n8n", "stored job n8n", "n8n_callback", "n8n callback",
    "force_rerun", "force rerun", "resume", "application_package",
    "application package",
)


def plain(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def raw_text(job: dict[str, Any], *keys: str) -> str:
    values: list[str] = []
    for key in keys:
        value = job.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list, tuple, set)):
            values.append(json.dumps(value, ensure_ascii=False, default=str))
        else:
            values.append(str(value))
    return " | ".join(values)


@lru_cache(maxsize=1)
def country_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for code, names in STATIC_COUNTRIES.items():
        aliases[plain(code)] = code
        for name in names:
            normalized = plain(name)
            if normalized:
                aliases[normalized] = code
    aliases.update(MANUAL_COUNTRY_ALIASES)

    # Use the project's own country catalog as an additional source when it is
    # available, without making it a runtime requirement.
    try:
        from app.geo_data import get_countries
        for item in get_countries():
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip().upper()
                names: Iterable[Any] = (item.get("name"), item.get("label"), item.get("code"))
            else:
                code = str(getattr(item, "code", "") or "").strip().upper()
                names = (
                    getattr(item, "name", None),
                    getattr(item, "label", None),
                    getattr(item, "code", None),
                )
            if len(code) != 2:
                continue
            aliases[plain(code)] = code
            for name in names:
                normalized = plain(name)
                if normalized:
                    aliases[normalized] = code
    except Exception:
        pass
    return aliases


def normalize_country_code(value: Any) -> str | None:
    normalized = plain(value)
    if not normalized:
        return None
    aliases = country_aliases()
    if normalized in aliases:
        return aliases[normalized]
    compact = normalized.replace(" ", "").upper()
    if len(compact) == 2 and compact.isalpha():
        return compact
    return None


def foreign_country_mentions(location_text: str) -> list[dict[str, str]]:
    normalized = plain(location_text)
    if not normalized:
        return []
    candidates = sorted(
        (
            (alias, code)
            for alias, code in country_aliases().items()
            if code != "US"
            and len(alias) >= 4
            and alias not in AMBIGUOUS_COUNTRY_NAMES
            and not re.fullmatch(r"[a-z]{2,3}", alias)
        ),
        key=lambda value: len(value[0]),
        reverse=True,
    )
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for alias, code in candidates:
        if code in seen:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized):
            seen.add(code)
            matches.append({"code": code, "alias": alias})
    return matches


def us_state_evidence(job: dict[str, Any], location_text: str) -> dict[str, str] | None:
    state_value = str(
        job.get("state") or job.get("region") or job.get("province") or ""
    ).strip()
    state_code = state_value.upper()
    if state_code in US_STATE_CODES:
        return {"state": state_code, "evidence": "job_state_code"}
    normalized_state = plain(state_value)
    if normalized_state in US_STATE_NAMES:
        return {"state": US_STATE_NAMES[normalized_state], "evidence": "job_state_name"}

    normalized_location = plain(location_text)
    for state_name, code in US_STATE_NAMES.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(state_name)}(?![a-z0-9])", normalized_location):
            return {"state": code, "evidence": "location_state_name"}

    # State abbreviations are accepted only in location-like punctuation
    # contexts. This avoids interpreting words such as "in" as Indiana.
    upper_location = str(location_text or "").upper()
    for code in sorted(US_STATE_CODES):
        if re.search(rf"(?:^|[,(/-]\s*){re.escape(code)}(?:\s*[,)/-]|$)", upper_location):
            return {"state": code, "evidence": "location_state_code"}
    return None


def classify_arrangement(job: dict[str, Any]) -> str:
    text = plain(raw_text(
        job,
        "remote_type", "work_arrangement", "workplace_type", "work_mode",
        "location_raw", "location",
    ))
    if re.search(r"\bhybrid\b", text):
        return "hybrid"
    if re.search(r"\b(remote|work from home|wfh|telecommute|distributed)\b", text):
        return "remote"
    if re.search(r"\b(on site|onsite|in office|office based)\b", text):
        return "onsite"
    return "unknown"


def classify_job_location(job: dict[str, Any]) -> dict[str, Any]:
    location_text = raw_text(
        job,
        "location_raw", "location", "job_location", "formatted_location",
        "city", "state", "region", "province",
    )
    foreign = foreign_country_mentions(location_text)

    explicit_raw = (
        job.get("_provider_country_raw")
        or job.get("country_code")
        or job.get("job_country")
        or job.get("country")
    )
    explicit_code = normalize_country_code(explicit_raw)
    explicit_flag = job.get("_country_explicit")
    explicit_plain = plain(explicit_raw)
    descriptive_us = explicit_plain in {
        "united states", "united states of america",
    }

    state = us_state_evidence(job, location_text)
    normalized_location = plain(location_text)
    us_name_evidence = bool(re.search(
        r"(?<![a-z0-9])(united states|united states of america|usa|u s a)(?![a-z0-9])",
        normalized_location,
    ))
    remote_us_evidence = bool(re.search(
        r"(?:^|[|,(/-]\s*)(us|u s|usa|u s a)(?:\s*[,|)/-]|$)",
        normalized_location,
    ))

    country_conflict = bool(
        foreign
        and explicit_code
        and any(item["code"] != explicit_code for item in foreign)
    )

    if foreign:
        inferred_country = foreign[0]["code"]
        evidence = f"foreign_location_name:{foreign[0]['alias']}"
    elif explicit_code and explicit_code != "US":
        inferred_country = explicit_code
        evidence = "explicit_foreign_country"
    elif state:
        inferred_country = "US"
        evidence = state["evidence"]
    elif us_name_evidence or remote_us_evidence:
        inferred_country = "US"
        evidence = "location_united_states"
    elif explicit_code == "US" and (explicit_flag is True or descriptive_us):
        inferred_country = "US"
        evidence = "explicit_us_country"
    else:
        inferred_country = None
        evidence = "country_unknown_fail_closed"

    arrangement = classify_arrangement(job)
    generic_remote = bool(
        arrangement == "remote"
        and plain(location_text) in {
            "", "remote", "work from home", "wfh", "anywhere", "not specified",
        }
    )

    return {
        "country": inferred_country,
        "country_evidence": evidence,
        "country_conflict": country_conflict,
        "foreign_location_mentions": foreign,
        "arrangement": arrangement,
        "state": state["state"] if state else None,
        "city": str(job.get("city") or "").strip() or None,
        "location_text": location_text,
        "generic_remote": generic_remote,
        "explicit_country_raw": explicit_raw,
        "explicit_country_flag": explicit_flag,
    }


def arrangement_allowed(profile: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    arrangement = str(profile.get("arrangement") or "unknown")
    if arrangement == "remote":
        return bool(rule.get("remote_allowed")), arrangement
    if arrangement == "hybrid":
        return bool(rule.get("hybrid_allowed")), arrangement
    if arrangement == "onsite":
        return bool(rule.get("onsite_allowed")), arrangement

    # Missing arrangement metadata is accepted only for a concrete location
    # and only when the rule permits a place-based mode. It can never satisfy
    # a remote-only rule.
    concrete = bool(
        profile.get("state")
        or profile.get("city")
        or plain(profile.get("location_text")) not in {
            "", "not specified", "remote", "anywhere", "work from home",
        }
    )
    place_mode_allowed = bool(rule.get("hybrid_allowed") or rule.get("onsite_allowed"))
    return bool(concrete and place_mode_allowed), arrangement


def external_location_match(job: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    try:
        from app.hunter_worker import matches_location_rule
    except Exception:
        return False, "existing_location_matcher_unavailable"
    attempts = (
        lambda: matches_location_rule(job, rule),
        lambda: matches_location_rule(job.get("location_raw"), rule),
        lambda: matches_location_rule(job.get("location_raw"), job.get("remote_type"), rule),
        lambda: matches_location_rule(
            location_raw=job.get("location_raw"),
            remote_type=job.get("remote_type"),
            rule=rule,
        ),
    )
    for attempt in attempts:
        try:
            result = attempt()
        except (TypeError, AttributeError, KeyError):
            continue
        except Exception:
            return False, "existing_location_matcher_error"
        if isinstance(result, tuple):
            return bool(result[0]), str(result[1] if len(result) > 1 else "existing_match")
        if isinstance(result, dict):
            return bool(result.get("matched", result.get("match", False))), str(
                result.get("reason") or "existing_match"
            )
        return bool(result), "existing_match"
    return False, "existing_location_matcher_signature_mismatch"


def match_rule(
    job: dict[str, Any],
    rule: dict[str, Any],
    *,
    profile: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    rule_country = normalize_country_code(rule.get("country")) or "US"
    detail = {
        "rule_id": rule.get("rule_id") or rule.get("id"),
        "rule_name": rule.get("rule_name") or rule.get("location_name"),
        "rule_type": rule.get("rule_type") or rule.get("location_type"),
        "rule_country": rule_country,
        "job_country": profile.get("country"),
        "country_evidence": profile.get("country_evidence"),
        "arrangement": profile.get("arrangement"),
    }

    if profile.get("country_conflict"):
        return False, "country_evidence_conflict", detail
    if not profile.get("country"):
        return False, "country_unknown_fail_closed", detail
    if profile.get("country") != rule_country:
        return False, "country_not_allowed_by_rule", detail

    arrangement_ok, arrangement = arrangement_allowed(profile, rule)
    detail["arrangement"] = arrangement
    if not arrangement_ok:
        return False, "work_arrangement_not_allowed", detail

    rule_type = plain(rule.get("rule_type") or rule.get("location_type"))
    remote_only = bool(rule.get("remote_only")) or (
        bool(rule.get("remote_allowed"))
        and not bool(rule.get("hybrid_allowed"))
        and not bool(rule.get("onsite_allowed"))
    )
    if remote_only:
        if profile.get("arrangement") != "remote":
            return False, "remote_only_rule_requires_remote", detail
        return True, "strict_remote_country_match", detail

    if rule_type == "country":
        return True, "strict_country_match", detail

    target_state = str(rule.get("state") or "").strip().upper()
    target_city = plain(rule.get("city"))
    job_state = str(profile.get("state") or job.get("state") or "").strip().upper()
    job_city = plain(profile.get("city") or job.get("city"))
    location_plain = plain(profile.get("location_text"))

    if rule_type in {"state", "province"}:
        if target_state and job_state == target_state:
            return True, "strict_state_match", detail
        existing, reason = external_location_match(job, rule)
        if existing:
            return True, f"strict_country_then_{reason}", detail
        return False, "state_not_targeted", detail

    if rule_type == "city":
        direct_city = bool(
            target_city
            and (
                job_city == target_city
                or re.search(rf"(?<![a-z0-9]){re.escape(target_city)}(?![a-z0-9])", location_plain)
            )
        )
        state_ok = not target_state or not job_state or job_state == target_state
        if direct_city and state_ok:
            return True, "strict_city_match", detail
        existing, reason = external_location_match(job, rule)
        if existing:
            return True, f"strict_country_then_{reason}", detail
        return False, "city_or_commute_not_targeted", detail

    if rule_type == "region":
        name = plain(
            rule.get("rule_name")
            or rule.get("location_name")
            or rule.get("search_location")
        )
        if not rule.get("city") and not rule.get("state"):
            return True, "strict_country_region_match", detail
        if name and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", location_plain):
            return True, "strict_region_match", detail
        existing, reason = external_location_match(job, rule)
        if existing:
            return True, f"strict_country_then_{reason}", detail
        return False, "region_not_targeted", detail

    existing, reason = external_location_match(job, rule)
    if existing:
        return True, f"strict_country_then_{reason}", detail
    return False, "location_rule_not_matched", detail


def evaluate_location_plan(
    job: dict[str, Any],
    location_plan: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    rules = [dict(rule) for rule in location_plan]
    profile = classify_job_location(job)
    allowed_countries = sorted({
        normalize_country_code(rule.get("country")) or "US"
        for rule in rules
    })

    if not rules:
        return {
            "accepted": False,
            "reason": "dashboard_location_rules_empty",
            "profile": profile,
            "allowed_countries": allowed_countries,
            "matches": [],
        }
    if profile.get("country_conflict"):
        return {
            "accepted": False,
            "reason": "country_evidence_conflict",
            "profile": profile,
            "allowed_countries": allowed_countries,
            "matches": [],
        }
    if not profile.get("country"):
        return {
            "accepted": False,
            "reason": "country_unknown_fail_closed",
            "profile": profile,
            "allowed_countries": allowed_countries,
            "matches": [],
        }
    if profile.get("country") not in allowed_countries:
        return {
            "accepted": False,
            "reason": "country_not_in_dashboard_rules",
            "profile": profile,
            "allowed_countries": allowed_countries,
            "matches": [],
        }

    matches: list[dict[str, Any]] = []
    rejections: list[str] = []
    for rule in rules:
        matched, reason, detail = match_rule(job, rule, profile=profile)
        if matched:
            matches.append({**detail, "reason": reason})
        else:
            rejections.append(reason)
    return {
        "accepted": bool(matches),
        "reason": "strict_dashboard_location_match" if matches else "no_dashboard_location_rule_matched",
        "profile": profile,
        "allowed_countries": allowed_countries,
        "matches": matches,
        "rejection_reasons": sorted(set(rejections)),
    }


def canonicalize_job(job: dict[str, Any], location_result: dict[str, Any]) -> dict[str, Any]:
    output = dict(job)
    profile = dict(location_result.get("profile") or {})
    if profile.get("country"):
        output["country"] = profile["country"]
    if profile.get("state") and not output.get("state"):
        output["state"] = profile["state"]
    output["_dashboard_location_profile"] = profile
    output["_dashboard_location_matches"] = list(location_result.get("matches") or [])
    output["_strict_dashboard_targeting"] = True
    return output


def should_gate_actor(raw_job: dict[str, Any], actor: str) -> bool:
    actor_text = plain(actor)
    entry_path = plain(raw_job.get("entry_path"))
    status = plain(raw_job.get("status"))
    source = plain(raw_job.get("source"))
    combined = " ".join((actor_text, entry_path, status, source))
    if any(plain(term) in combined for term in EXEMPT_PATH_TERMS):
        return False
    if status.replace(" ", "_") in PROTECTED_STATUSES:
        return False
    if "fake worker" in source or "synthetic test" in entry_path:
        return False
    # Fail closed: every non-exempt central save is treated as adapter discovery.
    return True


def protected_non_discovery_row(row: dict[str, Any]) -> bool:
    status = plain(row.get("status")).replace(" ", "_")
    if status in PROTECTED_STATUSES:
        return True
    combined = plain(" ".join(
        str(row.get(key) or "")
        for key in ("source", "entry_path", "status")
    ))
    if any(plain(term) in combined for term in EXEMPT_PATH_TERMS):
        return True
    if "fake worker" in combined or "synthetic test" in combined:
        return True
    return False


def quarantine_unsent_adapter_jobs(
    *,
    source_prefix: str | None = None,
    limit: int = 10000,
) -> dict[str, Any]:
    from app.database import get_connection
    from app.dashboard_targeting_gate import evaluate_dashboard_job, load_dashboard_targeting_rules

    rules = load_dashboard_targeting_rules()
    read_connection = get_connection()
    try:
        columns = {str(row[1]) for row in read_connection.execute("PRAGMA table_info(jobs)")}
        if "telegram_sent" not in columns:
            return {"checked": 0, "quarantined": 0, "reason": "telegram_sent_column_missing", "changes": []}
        preferred = (
            "id", "source", "status", "entry_path", "title", "company_name",
            "location_raw", "city", "state", "country", "remote_type",
            "description_raw", "employment_type", "salary_raw",
            "qualifications", "preferred_qualifications", "responsibilities",
            "work_authorization", "telegram_sent", "match_label",
            "hard_rejection_reason",
        )
        selected = [column for column in preferred if column in columns]
        rows = [
            dict(row)
            for row in read_connection.execute(
                (
                    "SELECT "
                    + ", ".join(f'"{column}"' for column in selected)
                    + " FROM jobs WHERE COALESCE(telegram_sent, 0)=0 "
                      "ORDER BY id LIMIT ?"
                ),
                (max(1, min(int(limit), 50000)),),
            ).fetchall()
        ]
    finally:
        read_connection.close()

    normalized_prefix = plain(source_prefix)
    rejected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    checked = 0
    for row in rows:
        if protected_non_discovery_row(row):
            continue
        if normalized_prefix and not plain(row.get("source")).startswith(normalized_prefix):
            continue
        checked += 1
        result = evaluate_dashboard_job(row, rules=rules, require_location=True)
        if not result.get("accepted"):
            rejected.append((row, result))

    if not rejected:
        return {"checked": checked, "quarantined": 0, "source_prefix": source_prefix, "changes": []}

    changes: list[dict[str, Any]] = []
    connection = get_connection()
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")}
        for row, result in rejected:
            assignments = ["telegram_sent=-1"]
            parameters: list[Any] = []
            reason = str(result.get("location_rejection_reason") or result.get("reason") or "dashboard_targeting_rejected")
            evidence = {
                "reason": result.get("canonical_reason") or result.get("reason"),
                "role": result.get("role_evidence") or {},
                "experience": result.get("experience_evidence") or [],
                "hard_requirement": result.get("hard_requirement_evidence") or {},
                "company": result.get("company_evidence") or {},
                "location": result.get("location_evidence") or {},
                "preference": result.get("preference") or {},
                "quarantined_before_telegram": True,
            }
            if "status" in columns:
                assignments.append("status=?")
                parameters.append("rejected_by_dashboard_targeting")
            if "primary_decision" in columns:
                assignments.append("primary_decision=?")
                parameters.append(str(result.get("primary_category") or "REJECT_OTHER_TARGETING"))
            if "secondary_reasons_json" in columns:
                assignments.append("secondary_reasons_json=?")
                parameters.append(json.dumps(result.get("secondary_reasons") or [], ensure_ascii=False))
            if "decision_evidence_json" in columns:
                assignments.append("decision_evidence_json=?")
                parameters.append(json.dumps(evidence, ensure_ascii=False, default=str))
            if "targeting_rules_version" in columns:
                assignments.append("targeting_rules_version=?")
                parameters.append(str(result.get("rules_version") or ""))
            if "targeting_rules_hash" in columns:
                assignments.append("targeting_rules_hash=?")
                parameters.append(str(result.get("rules_hash") or ""))
            if "role_evidence_json" in columns:
                assignments.append("role_evidence_json=?")
                parameters.append(json.dumps(result.get("role_evidence") or {}, ensure_ascii=False, default=str))
            if "experience_evidence_json" in columns:
                assignments.append("experience_evidence_json=?")
                parameters.append(json.dumps(result.get("experience_evidence") or [], ensure_ascii=False, default=str))
            if "location_evidence_json" in columns:
                assignments.append("location_evidence_json=?")
                parameters.append(json.dumps(result.get("location_evidence") or {}, ensure_ascii=False, default=str))
            if "target_track" in columns:
                assignments.append("target_track=NULL")
            if "hunter_score" in columns:
                assignments.append("hunter_score=0")
            if "preference_score" in columns:
                assignments.append("preference_score=0")
            if "match_label" in columns:
                assignments.append("match_label=?")
                parameters.append("REJECTED")
            if "hard_rejection_reason" in columns:
                assignments.append("hard_rejection_reason=?")
                parameters.append(f"dashboard_targeting:{reason}")
            if "updated_at" in columns:
                assignments.append("updated_at=CURRENT_TIMESTAMP")
            parameters.append(int(row["id"]))
            cursor = connection.execute(
                "UPDATE jobs SET " + ", ".join(assignments)
                + " WHERE id=? AND COALESCE(telegram_sent,0)=0",
                parameters,
            )
            if cursor.rowcount:
                changes.append({
                    "id": int(row["id"]),
                    "before": {
                        "telegram_sent": row.get("telegram_sent"),
                        "status": row.get("status"),
                        "match_label": row.get("match_label"),
                        "hard_rejection_reason": row.get("hard_rejection_reason"),
                    },
                    "reason": reason,
                })
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    reasons: dict[str, int] = {}
    for change in changes:
        reasons[change["reason"]] = reasons.get(change["reason"], 0) + 1
    return {
        "checked": checked,
        "quarantined": len(changes),
        "source_prefix": source_prefix,
        "reasons": reasons,
        "changes": changes,
    }
