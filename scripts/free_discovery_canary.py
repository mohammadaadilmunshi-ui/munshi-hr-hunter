"""Two fixed public GET canaries; canonical targeting/dedupe reads only.

Requires an existing database opened with mode=ro and query_only. Never calls
save_job, initializers, migrations, schedulers, Telegram or an application form.
--emit-stdin-bundle permits reviewable source testing without deploying or
writing any files into the production container. The emitted Python contains
only the explicitly listed source modules and public board contract.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = (
    "app.discovery_policy", "app.public_board_http", "app.public_board_contracts",
    "app.public_jobposting", "app.sources.teamtailor", "app.sources.ashby",
    "app.dashboard_targeting_gate",
)


def run(database_path: Path, providers: tuple[str, ...] = ('Teamtailor', 'Ashby')) -> dict:
    # Override the connection factory before importing the pipeline. Every
    # imported binding in this fresh process receives the read-only factory.
    import app.database as database

    def read_only_connection():
        c = sqlite3.connect(database_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        return c

    database.get_connection = read_only_connection
    from app.dashboard_targeting_gate import filter_dashboard_jobs
    from app.targeting import load_rules
    from app.job_store import prepare_job, find_existing_job
    from app.public_board_http import PublicBoardClient
    from app.sources.teamtailor import parse_teamtailor_feed
    from app.sources.ashby import parse_ashby_payload

    rules = load_rules()
    contract = {
        'company_name': 'Teamtailor', 'provider': 'Teamtailor',
        'board_url': 'https://career.teamtailor.com/jobs.rss', 'job_path_prefix': '/jobs/',
    }
    board_specs = (
        ('Teamtailor', 'Teamtailor', 'https://career.teamtailor.com/jobs.rss?per_page=2'),
        ('Ashby', 'Decagon', 'https://api.ashbyhq.com/posting-api/job-board/decagon'),
    )
    outcomes = []
    for provider, company, url in board_specs:
        if provider not in providers:
            continue
        from urllib.parse import urlsplit
        client = PublicBoardClient({str(urlsplit(url).hostname)}, max_requests=2, max_bytes=10_000_000)
        try:
            response = client.get(url)
            if provider == 'Teamtailor':
                parsed = parse_teamtailor_feed(response.content, contract)
                jobs = parsed['jobs']; raw = parsed['raw_count']
                normalization_rejections = parsed['rejections']
            else:
                payload = response.json()
                raw = len(payload.get('jobs') or [])
                jobs = parse_ashby_payload(company_name=company, board_name='decagon', payload=payload)
                normalization_rejections = {'unlisted_or_malformed': raw - len(jobs)}
            funnel = filter_dashboard_jobs(jobs, rules=rules)
            c = read_only_connection()
            try:
                duplicates = sum(find_existing_job(c, prepare_job(j))[0] is not None for j in funnel['eligible_jobs'])
            finally:
                c.close()
            outcomes.append({
                'provider': provider, 'company': company, 'endpoint': url,
                'status': 'VERIFIED_LIVE_NO_STORE', 'http_status': response.status_code,
                'response_sha256': hashlib.sha256(response.content).hexdigest(),
                'requests': client.requests, 'raw': raw, 'normalized': len(jobs),
                'normalization_rejections': normalization_rejections,
                'eligible_before_database_dedupe': funnel['eligible'],
                'duplicates_within_run': funnel['duplicates_within_run'],
                'database_duplicates': duplicates, 'would_insert': funnel['eligible'] - duplicates,
                'new': 0, 'stored': 0, 'accounting_delta': funnel['accounting_delta'],
                'reject_role': funnel['reject_role'], 'reject_location': funnel['reject_location'],
                'reject_hard_requirement': funnel['reject_hard_requirement'],
                'examples': [{k: j.get(k) for k in ('company_name', 'title', 'ats_job_id', 'location_raw', 'job_url', 'apply_url')} for j in jobs[:2]],
            })
        except Exception as error:
            outcomes.append({'provider': provider, 'company': company, 'endpoint': url,
                             'status': 'BLOCKED_OR_FAILED', 'error': str(error), 'requests': client.requests,
                             'new': 0, 'stored': 0})
    return {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'no_store': True,
            'production_database_writes': 0, 'paid_api_requests': 0, 'application_submissions': 0,
            'rules_hash': rules.rules_hash, 'outcomes': outcomes}


def stdin_bundle(database_path: Path, providers: tuple[str, ...] = ('Teamtailor', 'Ashby')) -> str:
    modules = {name: (ROOT / (name.replace('.', '/') + '.py')).read_text() for name in OVERLAY}
    hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in modules.items()}
    return (
        'import sys, types, json, hashlib\n'
        'from pathlib import Path\n'
        'import sqlite3, app.database as database\n'
        f'canary_database = Path({str(database_path)!r})\n'
        'def readonly_connection():\n'
        ' c = sqlite3.connect(canary_database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)\n'
        ' c.row_factory = sqlite3.Row\n'
        ' c.execute("PRAGMA query_only=ON")\n'
        ' return c\n'
        'database.get_connection = readonly_connection\n'
        f'modules = {modules!r}\n'
        f'expected = {hashes!r}\n'
        'for name, source in modules.items():\n'
        ' assert hashlib.sha256(source.encode()).hexdigest() == expected[name]\n'
        ' module = types.ModuleType(name)\n'
        ' module.__file__ = "/app/hunter/" + name.replace(".", "/") + ".py"\n'
        ' sys.modules[name] = module\n'
        ' exec(compile(source, module.__file__, "exec"), module.__dict__)\n'
        f'namespace = {{"__name__": "canary", "__file__": "/app/hunter/scripts/free_discovery_canary.py"}}\n'
        f'exec(compile({Path(__file__).read_text()!r}, namespace["__file__"], "exec"), namespace)\n'
        f'result = namespace["run"](Path({str(database_path)!r}), {providers!r})\n'
        'result["source_module_sha256"] = expected\n'
        'print(json.dumps(result, indent=2))\n'
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--emit-stdin-bundle', action='store_true')
    parser.add_argument('--provider', action='append', choices=('Teamtailor', 'Ashby'))
    args = parser.parse_args()
    if args.emit_stdin_bundle:
        print(stdin_bundle(args.database, tuple(args.provider or ('Teamtailor', 'Ashby'))))
    else:
        sys.path.insert(0, str(ROOT))
        print(json.dumps(run(args.database, tuple(args.provider or ('Teamtailor', 'Ashby'))), indent=2))
