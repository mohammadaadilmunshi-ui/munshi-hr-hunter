from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import N8nStatusUpdate
from app.n8n_dispatch import build_payload


INTEGRATION_CONFIG = ROOT / "config" / "integration_health_policy.json"
CONTRACT_CONFIG = ROOT / "config" / "downstream_contract.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def synthetic_payload() -> dict[str, Any]:
    job = {
        "id": 0,
        "job_fingerprint": "0" * 64,
        "source": "contract-validator",
        "company_name": "Contract Validation Fixture",
        "title": "People Analytics Analyst",
        "location_raw": "United States",
        "country": "US",
        "description_raw": "Analyze workforce metrics and maintain HR dashboards.",
        "hunter_score": 100,
        "match_label": "fixture",
        "target_track": "People Analytics",
    }
    queue = {
        "id": 0,
        "idempotency_key": "0" * 64,
        "request_id": "contract_validation_fixture",
        "dispatch_mode": "telegram_manual",
    }
    return build_payload(job, queue, webhook_mode="production")


def referenced_json_fields(nodes_text: str) -> list[str]:
    patterns = (
        r"\$json\.([A-Za-z_][A-Za-z0-9_]*)",
        r"\$json\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]",
    )
    return sorted({match for pattern in patterns for match in re.findall(pattern, nodes_text)})


def safe_node_summary(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for node in nodes:
        item: dict[str, Any] = {
            "name": str(node.get("name") or ""),
            "type": str(node.get("type") or ""),
            "disabled": bool(node.get("disabled", False)),
        }
        if "webhook" in item["type"].casefold():
            parameters = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
            item["http_method"] = str(parameters.get("httpMethod") or "GET")
            item["path"] = str(parameters.get("path") or "")
        summaries.append(item)
    return summaries


def service_health(services: dict[str, Any]) -> dict[str, Any]:
    n8n = services.get("n8n") if isinstance(services.get("n8n"), dict) else {}
    host = str(n8n.get("host") or "127.0.0.1")
    port = int(n8n.get("port") or 5678)
    url = f"http://{host}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return {"reachable": response.status == 200, "http_status": response.status}
    except Exception as error:
        return {"reachable": False, "error_type": type(error).__name__}


def execution_summary(connection: sqlite3.Connection, workflow_id: str) -> dict[str, Any]:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_entity'"
    ).fetchone()
    if not table:
        return {"available": False}
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(execution_entity)")
    }
    workflow_column = "workflowId" if "workflowId" in columns else None
    if workflow_column is None:
        return {"available": False, "reason": "workflow_identity_column_missing"}
    total = connection.execute(
        f'SELECT COUNT(*) FROM execution_entity WHERE "{workflow_column}"=?',
        (workflow_id,),
    ).fetchone()[0]
    select = [name for name in ("id", "status", "startedAt", "stoppedAt") if name in columns]
    latest: dict[str, Any] | None = None
    if select:
        quoted = ", ".join('"' + name + '"' for name in select)
        row = connection.execute(
            f'SELECT {quoted} FROM execution_entity WHERE "{workflow_column}"=? '
            'ORDER BY "id" DESC LIMIT 1',
            (workflow_id,),
        ).fetchone()
        latest = dict(zip(select, row)) if row is not None else None
    return {"available": True, "total": int(total), "latest": latest}


def validate(output: Path) -> dict[str, Any]:
    integration = read_json(INTEGRATION_CONFIG)
    contract = read_json(CONTRACT_CONFIG)
    baseline = integration.get("n8n_read_only_snapshot") or {}
    workflow_id = str(baseline.get("workflow_id") or "")
    database_path = Path(str(integration.get("n8n_database_path") or ""))
    if not workflow_id or not database_path.is_file():
        raise RuntimeError("The configured read-only n8n workflow/database is unavailable.")

    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        data_version_before = int(connection.execute("PRAGMA data_version").fetchone()[0])
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        row = connection.execute(
            """
            SELECT id, name, active, versionId, activeVersionId, updatedAt, nodes, connections
            FROM workflow_entity WHERE id=?
            """,
            (workflow_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Configured n8n workflow {workflow_id} was not found.")
        nodes_text = str(row["nodes"] or "[]")
        connections_text = str(row["connections"] or "{}")
        nodes = json.loads(nodes_text)
        if not isinstance(nodes, list):
            raise RuntimeError("n8n workflow nodes are not a JSON list.")
        executions = execution_summary(connection, workflow_id)
        data_version_after = int(connection.execute("PRAGMA data_version").fetchone()[0])
    finally:
        connection.close()

    generated = synthetic_payload()
    required_payload = {str(value) for value in contract.get("required_payload_fields") or []}
    ingress_required = {str(value) for value in contract.get("workflow_ingress_required_fields") or []}
    mentioned = {
        key for key in generated
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", nodes_text)
    }
    json_fields = set(referenced_json_fields(nodes_text))
    workflow_visible_fields = mentioned | json_fields

    # The captured baseline was produced from sqlite3 CLI output, which adds
    # one trailing newline to each selected text value.  Preserve that exact
    # byte contract and also report raw-field hashes for future comparisons.
    nodes_hash = sha256_text(nodes_text + "\n")
    connections_hash = sha256_text(connections_text + "\n")
    hash_match = (
        nodes_hash == str(baseline.get("nodes_sha256") or "")
        and connections_hash == str(baseline.get("connections_sha256") or "")
    )
    version_match = str(row["activeVersionId"] or row["versionId"] or "") == str(
        baseline.get("active_version_id") or ""
    )
    payload_missing = sorted(required_payload - set(generated))
    ingress_missing = sorted(ingress_required - workflow_visible_fields)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "n8n_mutated": False,
        "database": {
            "path": str(database_path),
            "opened_with": "sqlite_uri_mode_ro_and_query_only",
            "quick_check": quick_check,
            "data_version_before": data_version_before,
            "data_version_after": data_version_after,
        },
        "workflow": {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "active": bool(row["active"]),
            "version_id": str(row["versionId"] or ""),
            "active_version_id": str(row["activeVersionId"] or ""),
            "updated_at": str(row["updatedAt"] or ""),
            "node_count": len(nodes),
            "nodes_sha256": nodes_hash,
            "connections_sha256": connections_hash,
            "hash_encoding": "utf8_sqlite_cli_text_with_trailing_newline",
            "raw_nodes_sha256": sha256_text(nodes_text),
            "raw_connections_sha256": sha256_text(connections_text),
            "baseline_hash_match": hash_match,
            "baseline_version_match": version_match,
            "nodes": safe_node_summary(nodes),
        },
        "contract": {
            "generated_payload_fields": sorted(generated),
            "required_payload_fields": sorted(required_payload),
            "missing_generated_fields": payload_missing,
            "workflow_referenced_json_fields": sorted(json_fields),
            "workflow_mentions_generated_fields": sorted(mentioned),
            "workflow_ingress_required_fields": sorted(ingress_required),
            "missing_workflow_ingress_fields": ingress_missing,
            "callback_model_fields": sorted(N8nStatusUpdate.model_fields),
            "callback_url": str(contract.get("callback_url") or ""),
        },
        "recent_executions": executions,
        "service": service_health(integration.get("services") or {}),
        "acceptance": {
            "database_ok": quick_check == "ok",
            "workflow_unchanged": hash_match and version_match,
            "payload_complete": not payload_missing,
            "workflow_ingress_compatible": not ingress_missing,
        },
        "redaction": {
            "node_parameters_omitted": True,
            "credentials_omitted": True,
            "execution_payloads_omitted": True,
        },
    }
    report["success"] = all(report["acceptance"].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the frozen n8n contract read-only.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "reports" / f"AADIL_HR_HUNTER_N8N_READ_ONLY_{utc_now()}.json"
    report = validate(output)
    print(
        json.dumps(
            {
                "success": report["success"],
                "output": str(output),
                "acceptance": report["acceptance"],
                "n8n_mutated": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
