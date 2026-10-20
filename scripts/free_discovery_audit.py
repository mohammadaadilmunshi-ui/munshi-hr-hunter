"""Sanitized, read-only source evidence. No worker starts or private job data.

Can be streamed over SSH to the production container's Python interpreter.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


def audit(connection: sqlite3.Connection, source_root: Path) -> dict:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    def setting(key):
        row = connection.execute("SELECT value_json FROM settings WHERE setting_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}
    registry = setting("source_worker_registry")
    workers = registry.get("workers", {})
    normalize = lambda s: re.sub('[^a-z0-9]', '', s.lower())
    selected = {normalize(n): m for n, m in workers.items()}
    sources = []
    fields = "source_name source_tier enabled cost_mode health_status last_run_at consecutive_failures request_count_last_run raw_jobs_last_run normalized_jobs_last_run eligible_jobs_last_run inserted_jobs_last_run duplicate_jobs_last_run reject_role_last_run reject_location_last_run reject_hard_requirement_last_run reject_company_last_run reject_other_targeting_last_run accounting_delta_last_run provider_used_last_run".split()
    for row in connection.execute("SELECT * FROM source_health ORDER BY source_name"):
        row = dict(row)
        source = {k: row[k] for k in fields if k in row}
        name = source['source_name']
        module = selected.get(normalize(name), '')
        exists = bool(re.fullmatch(r'app\.[a-z][a-z0-9_]*', module)) and (source_root / (module.replace('.', '/') + '.py')).is_file()
        source.update(registered_module=module or None, module_exists=exists)
        source['error_classification'] = (
            'paid_policy_disabled' if source.get('cost_mode') != 'free'
            else 'missing_worker_module' if not exists
            else 'rate_limited' if '429' in str(row.get('last_error') or '')
            else 'worker_invocation_failed_unresolved' if source.get('health_status') == 'failed'
            else 'provider_degraded' if source.get('health_status') == 'degraded'
            else 'no_current_failure_evidence'
        )
        if 'source_runs' in tables:
            run = connection.execute("SELECT * FROM source_runs WHERE source_name=? ORDER BY completed_at DESC LIMIT 1", (name,)).fetchone()
            if run:
                run = dict(run)
                source['last_run'] = {k: run[k] for k in (
                    'run_id', 'completed_at', 'run_status', 'request_count', 'raw_count',
                    'normalized_count', 'eligible_count', 'duplicate_count', 'new_eligible_count',
                    'reject_role_count', 'reject_location_count', 'reject_hard_requirement_count',
                    'reject_company_count', 'reject_other_targeting_count', 'accounting_delta', 'error_count',
                ) if k in run}
                details = json.loads(run.get('detail_json') or '{}')
                source['last_run'].update({k: details[k] for k in ('worker_status', 'worker_returncode') if k in details})
        if 'source_random_schedule' in tables:
            schedule = connection.execute("SELECT schedule_state,last_worker_status,last_worker_returncode,consecutive_scheduler_failures FROM source_random_schedule WHERE source_name=?", (name,)).fetchone()
            source['schedule'] = dict(schedule) if schedule else None
        if 'public_adapter_boards' in tables:
            source['public_board_counts'] = dict(connection.execute("SELECT count(*) total,coalesce(sum(enabled),0) enabled FROM public_adapter_boards WHERE lower(source_name)=lower(?)", (name,)).fetchone())
        sources.append(source)
    return {
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'read_only', 'production_writes': 0, 'application_submissions': 0,
        'network_provider_requests': 0, 'registry_worker_count': len(workers),
        'source_count': len(sources),
        'enabled_failed_count': sum(s.get('enabled') == 1 and s.get('health_status') == 'failed' for s in sources),
        'enabled_missing_module_count': sum(s.get('enabled') == 1 and not s['module_exists'] for s in sources),
        'table_counts': {t: connection.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in (
            'public_adapter_boards', 'ats_company_registry', 'source_runs', 'source_run_stages',
        ) if t in tables},
        'sources': sources,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as connection:
        print(json.dumps(audit(connection, args.source_root), indent=2))
