"""Lossless parsing of confirmed, wrapped Master Resume certificates.

Never resolve issuer aliases, guess credential names, or edit Candidate Truth.
The parsed line is always a verbatim span from the confirmed source facts.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def complete_certifications(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for fact in snapshot.get("facts") or []:
        if not isinstance(fact, Mapping):
            continue
        match = re.fullmatch(r"certifications\.(\d+)\.name", str(fact.get("key") or ""))
        value = " ".join(str(fact.get("value") or "").split())
        fact_id = str(fact.get("fact_id") or "")
        if match and value and fact_id:
            facts.append((int(match.group(1)), value, "truth:" + fact_id))
    if not facts:
        return []
    facts.sort()
    if len({f[0] for f in facts}) != len(facts):
        raise ValueError("Duplicate confirmed certification indexes.")
    if not any("|" in f[1] for f in facts):
        return [
            {"name": value, "issuer": "", "evidence_ids": [evidence]}
            for _index, value, evidence in facts
        ]

    text, ranges = "", []
    for _index, value, evidence in facts:
        if text:
            text += " "
        start = len(text)
        text += value
        ranges.append((start, len(text), evidence))
    result = []
    for match in re.finditer(r"[^|]+", text):
        entry = " ".join(match.group().split())
        if not entry:
            continue
        sources = [
            evidence for start, end, evidence in ranges
            if start < match.end() and end > match.start()
        ]
        if not sources:
            raise ValueError("Certification entry has no confirmed fact evidence.")
        name, issuer = entry, ""
        if " - " in entry:
            name, issuer = entry.rsplit(" - ", 1)
        if not name.strip():
            raise ValueError("Malformed confirmed certification entry.")
        result.append({
            "name": name.strip(),
            "issuer": issuer.strip(),
            "evidence_ids": list(dict.fromkeys(sources)),
        })
    if len(result) > 24:
        raise ValueError("More certificate entries than the supported safe maximum.")
    return result
