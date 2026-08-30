#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

PROJECT = Path(os.environ.get("AADIL_HR_HUNTER_PROJECT", Path.home() / "Aadil-HR-Hunter"))
N8N_DB = Path.home() / ".n8n" / "database.sqlite"
HUNTER_DB = PROJECT / "data" / "hunter.db"
POLICY = PROJECT / "config" / "n8n_change_control_policy.json"
INTEGRATION = PROJECT / "config" / "integration_health_policy.json"

def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def ro(path: Path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c

policy = json.loads(POLICY.read_text(encoding="utf-8"))
integration = json.loads(INTEGRATION.read_text(encoding="utf-8"))
workflow_id = policy.get("canonical_workflow_id")
baseline = policy.get("pre_unfreeze_baseline") or {}

n = ro(N8N_DB)
quick = n.execute("PRAGMA quick_check").fetchone()[0]
integrity = n.execute("PRAGMA integrity_check").fetchone()[0]
fk = len(n.execute("PRAGMA foreign_key_check").fetchall())
row = n.execute("SELECT * FROM workflow_entity WHERE id=?", (workflow_id,)).fetchone()
if not row:
    raise SystemExit("canonical workflow missing")
d = dict(row)
nodes_raw = str(d.get("nodes") or "[]")
conns_raw = str(d.get("connections") or "{}")
nodes = json.loads(nodes_raw)
conns = json.loads(conns_raw)
running = n.execute(
    "SELECT COUNT(*) FROM execution_entity WHERE workflowId=? "
    "AND lower(coalesce(status,'')) IN ('new','running','waiting')",
    (workflow_id,)
).fetchone()[0]
n.close()

node_hash = sha(nodes_raw + "\n")
conn_hash = sha(conns_raw + "\n")

pending = 0
h = ro(HUNTER_DB)
cols = [r[1] for r in h.execute('PRAGMA table_info("n8n_dispatch_queue")')]
sc = next((x for x in ("queue_status","status","state") if x in cols), None)
if sc:
    states = ("pending","queued","accepted","running","new","waiting","retry","dispatched","in_progress")
    placeholders = ",".join("?" for _ in states)
    sql = (
        f'SELECT COUNT(*) FROM n8n_dispatch_queue '
        f'WHERE lower(coalesce("{sc}",\'\')) IN ({placeholders})'
    )
    pending = int(h.execute(sql, states).fetchone()[0])
h.close()

service = (integration.get("services") or {}).get("n8n") or {}
snapshot = integration.get("n8n_read_only_snapshot") or {}
control = integration.get("n8n_change_control") or {}

names = [x.get("name") for x in nodes if isinstance(x, dict)]
checks = [
    ("n8n DB quick_check", quick == "ok", quick),
    ("n8n DB integrity_check", integrity == "ok", integrity),
    ("n8n DB FK violations", fk == 0, fk),
    ("canonical workflow active", bool(d.get("active")), d.get("active")),
    ("workflow id preserved", d.get("id") == workflow_id, d.get("id")),
    ("no duplicate node names", len(set(names)) == len(names), len(names)),
    ("change-control mode", policy.get("mode") == "controlled_mutation", policy.get("mode")),
    ("mutation allowed by policy", policy.get("mutation_allowed") is True, policy.get("mutation_allowed")),
    ("integration service not read-only", service.get("read_only") is False, service.get("read_only")),
    ("integration mutation_allowed", snapshot.get("mutation_allowed") is True, snapshot.get("mutation_allowed")),
    ("integration change-control mode", control.get("mode") == "controlled_mutation", control.get("mode")),
]

result = {
    "success": all(ok for _, ok, _ in checks),
    "change_control": {
        "mode": policy.get("mode"),
        "mutation_allowed": policy.get("mutation_allowed"),
        "workflow_id": workflow_id
    },
    "current_workflow": {
        "versionId": d.get("versionId"),
        "activeVersionId": d.get("activeVersionId"),
        "node_count": len(nodes),
        "connection_source_count": len(conns),
        "nodes_sha256": node_hash,
        "connections_sha256": conn_hash
    },
    "baseline_drift": {
        "nodes_changed": node_hash != baseline.get("nodes_sha256"),
        "connections_changed": conn_hash != baseline.get("connections_sha256"),
        "version_changed": (
            d.get("versionId") != baseline.get("version_id")
            or d.get("activeVersionId") != baseline.get("version_id")
        ),
        "meaning": "Expected after an authorized workflow mutation; investigate if no mutation was authorized."
    },
    "mutation_time_gates": {
        "running_like_executions": running,
        "open_hunter_dispatches": pending,
        "safe_for_structural_mutation_now": running == 0 and pending == 0
    },
    "checks": [{"check": n, "pass": ok, "detail": detail} for n, ok, detail in checks]
}
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["success"] else 1)
