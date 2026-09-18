"""Render a deployment copy of the immutable canonical n8n workflow offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/n8n_portability_contract.json"


def validate_base_url(value: str, name: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if not value or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be a nonempty HTTP(S) base URL")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain a query or fragment")
    return value


def render(fastapi_base_url: str, ollama_base_url: str, output: Path) -> dict[str, int]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    canonical = ROOT / contract["canonical_workflow"]
    source = canonical.read_bytes()
    if hashlib.sha256(source).hexdigest() != contract["canonical_workflow_sha256"]:
        raise ValueError("canonical workflow SHA-256 does not match the portability contract")
    if output.resolve() == canonical.resolve():
        raise ValueError("refusing to overwrite the canonical workflow")
    fastapi_base_url = validate_base_url(fastapi_base_url, "FASTAPI_BASE_URL")
    ollama_base_url = validate_base_url(ollama_base_url, "OLLAMA_BASE_URL")
    mappings = contract["endpoint_mappings"]
    replacements = {
        mappings["hr_agent_score"]["legacy_url"]: fastapi_base_url + mappings["hr_agent_score"]["path"],
        mappings["status_update"]["legacy_url"]: fastapi_base_url + mappings["status_update"]["path"],
        mappings["ollama_generate"]["legacy_url"]: ollama_base_url + mappings["ollama_generate"]["path"],
        mappings["n8n_webhook"]["legacy_url"]: contract["cloud_contract"]["deployment_endpoint_defaults"]["N8N_BASE_URL"],
    }
    rendered = source.decode("utf-8")
    summary: dict[str, int] = {}
    for old, new in replacements.items():
        count = rendered.count(old)
        expected = next(
            mapping["canonical_occurrences"]
            for mapping in mappings.values()
            if mapping["legacy_url"] == old
        )
        if count != expected:
            raise ValueError(
                f"canonical occurrence count for {old!r} was {count}, expected {expected}"
            )
        summary[old] = count
        rendered = rendered.replace(old, new)
    unresolved = [old for old in replacements if old in rendered]
    if unresolved:
        raise ValueError(
            "generated workflow retained classified loopback network endpoints: "
            + ", ".join(unresolved)
        )
    json.loads(rendered)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fastapi-base-url", default=os.getenv("FASTAPI_BASE_URL", "http://hunter:8000"))
    parser.add_argument("--ollama-base-url", default=os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = render(args.fastapi_base_url, args.ollama_base_url, args.output)
    print("Rendered n8n deployment copy:")
    for key, count in summary.items():
        print(f"  {key}: {count} replacement occurrence(s)")


if __name__ == "__main__":
    main()
