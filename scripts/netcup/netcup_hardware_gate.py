#!/usr/bin/env python3
"""Classify a read-only Netcup host forensic report before bootstrap mutation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MIN_CPU_COUNT = 8
MIN_MEMORY_KIB = 15_204_352  # 14.5 GiB
MIN_PRESENTED_DISK_BYTES = 480_000_000_000
MIN_ROOT_FREE_BYTES = 20_000_000_000


@dataclass(frozen=True)
class HardwareGateResult:
    errors: tuple[str, ...]
    cpu_model_evidence: str
    storage_classification: str

    @property
    def passed(self) -> bool:
        return not self.errors


def parse_forensic_report(text: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.replace("_", "").isalnum() and key == key.upper():
            facts.setdefault(key, value.strip())
    return facts


def _integer(facts: Mapping[str, str], key: str, errors: list[str]) -> int:
    raw = facts.get(key, "")
    try:
        return int(raw)
    except ValueError:
        errors.append(f"missing or invalid {key}")
        return 0


def classify_hardware(facts: Mapping[str, str]) -> HardwareGateResult:
    errors: list[str] = []
    if facts.get("UNAME_S") != "Linux":
        errors.append("not Linux")
    if facts.get("UNAME_M") != "x86_64":
        errors.append("not x86_64")
    if facts.get("OS_ID") != "ubuntu" or facts.get("OS_VERSION_ID") != "24.04":
        errors.append("not Ubuntu 24.04")

    if _integer(facts, "CPU_COUNT", errors) < MIN_CPU_COUNT:
        errors.append("fewer than 8 CPUs")

    cpu_model = facts.get("CPU_MODEL", "").strip()
    if not cpu_model or cpu_model.casefold() in {"unknown", "not exposed", "n/a"}:
        cpu_evidence = "UNAVAILABLE"
    elif "amd" in cpu_model.casefold() and "epyc" in cpu_model.casefold():
        cpu_evidence = "AMD_EPYC"
    else:
        cpu_evidence = "UNEXPECTED"
        errors.append("exposed CPU model is not AMD EPYC class")

    if _integer(facts, "MEM_KIB", errors) < MIN_MEMORY_KIB:
        errors.append("less than 14.5 GiB RAM")
    if _integer(facts, "PRESENTED_DISK_BYTES", errors) < MIN_PRESENTED_DISK_BYTES:
        errors.append("presented disk capacity is below approximately 480 GB")
    if _integer(facts, "ROOT_FREE_BYTES", errors) < MIN_ROOT_FREE_BYTES:
        errors.append("root filesystem has less than 20 GB free")

    storage_error_fragments = (
        "PRESENTED_DISK_BYTES",
        "ROOT_FREE_BYTES",
        "presented disk capacity",
        "root filesystem",
    )
    storage_classification = (
        "NO_GO_CAPACITY"
        if any(any(fragment in error for fragment in storage_error_fragments) for error in errors)
        else "PASS_VIRTUAL_BLOCK_CAPACITY"
    )
    return HardwareGateResult(tuple(errors), cpu_evidence, storage_classification)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("forensic_report", type=Path)
    args = parser.parse_args()
    facts = parse_forensic_report(args.forensic_report.read_text(encoding="utf-8"))
    result = classify_hardware(facts)
    print(f"CPU_MODEL_EVIDENCE={result.cpu_model_evidence}")
    print(f"STORAGE_CLASSIFICATION={result.storage_classification}")
    if result.passed:
        print("RESULT: GO_NETCUP_HARDWARE_GATE")
        return 0
    for error in result.errors:
        print(f"HARDWARE_GATE_ERROR={error}")
    print("RESULT: NO_GO_NETCUP_HARDWARE_MISMATCH")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
