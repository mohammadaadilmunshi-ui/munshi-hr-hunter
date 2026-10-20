from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
import json
from pathlib import Path
import socket

import pytest
import requests

from app import database, market_source_common as market, provider_adapter_common as common
from app.dashboard_targeting_gate import filter_dashboard_jobs
from app.job_store import save_job
from app.public_board_http import PublicBoardClient, PublicBoardError, retry_after_seconds, validate_board_url
from app.public_jobposting import parse_jobposting_html
from app.sources.teamtailor import fetch_teamtailor_board, parse_teamtailor_feed
from app.sources.ashby import normalize_ashby_job

HOST = "career.teamtailor.com"
URL = f"https://{HOST}/jobs.rss"
CONTRACT = {"provider": "Teamtailor", "company_name": "Teamtailor", "board_url": URL, "job_path_prefix": "/jobs/"}


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("Deterministic fixtures must not open a network connection")
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def feed(items: list[dict]) -> bytes:
    content = []
    for item in items:
        locations = "".join(
            f"<tt:location><tt:city>{city}</tt:city><tt:country>{country}</tt:country></tt:location>"
            for city, country in item.get("locations", [("New York", "United States")])
        )
        content.append(f'''<item><title>{item.get("title", "People Analytics Analyst")}</title>
          <link>{item.get("url", f"https://{HOST}/jobs/{item.get('id', 42)}-people-analytics-analyst")}</link>
          <description>{item.get("description", "Analyze workforce data and create HR dashboards.")}</description>
          <remoteStatus>hybrid</remoteStatus><tt:locations>{locations}</tt:locations></item>''')
    return (f'<rss xmlns:tt="https://teamtailor.com/locations"><channel><title>Teamtailor</title>' + ''.join(content) + '</channel></rss>').encode()


class Response:
    def __init__(self, status=200, content=b"", headers=None):
        self.status = status
        self.content = content
        self.headers = headers or {}
    def getheaders(self):
        return list(self.headers.items())
    def read(self, size):
        return self.content[:size]


class Wire:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = 0
    def __call__(self, host, ip, timeout):
        wire = self
        class Connection:
            def request(self, method, path, headers):
                assert method == 'GET'
                assert not {'Authorization', 'Cookie', 'Proxy-Authorization'} & headers.keys()
                wire.calls.append((host, ip, path))
            def getresponse(self):
                value = next(wire.responses)
                if isinstance(value, Exception):
                    raise value
                return value
            def close(self):
                wire.closed += 1
        return Connection()


def client(responses, **kwargs):
    wire = Wire(responses)
    dns = lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
    return PublicBoardClient({HOST}, dns_lookup=dns, connection_factory=wire, **kwargs), wire


def test_public_transport_robots_and_exact_ip_tls_boundary():
    c, wire = client([Response(404), Response(content=feed([{}]))], max_requests=2)
    assert c.get(URL).status_code == 200
    assert c.requests == 2
    assert wire.calls == [(HOST, '8.8.8.8', '/robots.txt'), (HOST, '8.8.8.8', '/jobs.rss')]
    assert wire.closed == 2


@pytest.mark.parametrize('url', [
    'http://career.teamtailor.com/jobs.rss', 'https://user:pass@career.teamtailor.com/jobs.rss',
    'https://127.0.0.1/jobs.rss', 'https://career.teamtailor.com:444/jobs.rss',
    'https://evil.example/jobs.rss', 'https://career.teamtailor.com/jobs.rss#apply',
    'https://career.teamtailor.com\\@evil.example/jobs.rss', 'https://api.apify.com/run',
])
def test_unapproved_urls_are_rejected_before_request(url):
    with pytest.raises(ValueError):
        validate_board_url(url, {HOST})


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1'])
def test_private_or_mixed_dns_never_connects(address):
    c, wire = client([])
    c.dns_lookup = lambda *a: [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', (address, 443))]
    with pytest.raises(ValueError, match='non-public'):
        c.get(URL)
    assert wire.calls == []


@pytest.mark.parametrize('status', [401, 403, 429])
def test_access_block_stops_host_and_honors_retry_after(status):
    c, wire = client([Response(404), Response(status, headers={'Retry-After': '28800'})])
    for _ in range(2):
        with pytest.raises(PublicBoardError) as error:
            c.get(URL)
        assert error.value.status == status
        assert error.value.retry_after_seconds == 28800
    assert c.requests == 2


@pytest.mark.parametrize('status', [302, 404, 410, 500])
def test_removed_redirect_and_server_failures_are_not_empty_success(status):
    c, wire = client([Response(404), Response(status, headers={'Location': 'https://api.apify.com/run'})])
    with pytest.raises(PublicBoardError) as error:
        c.get(URL)
    assert error.value.status == status
    assert len(wire.calls) == 2


def test_robots_disallow_and_captcha_are_respected():
    c, wire = client([Response(content=b'User-agent: *\nDisallow: /jobs.rss')])
    with pytest.raises(PublicBoardError, match='Robots'):
        c.get(URL)
    assert c.requests == 1
    c, wire = client([Response(404), Response(content=b'verify you are human')])
    with pytest.raises(PublicBoardError, match='CAPTCHA'):
        c.get(URL)


def test_timeout_and_byte_request_budgets_close_connections():
    c, wire = client([TimeoutError('fixture timeout')])
    with pytest.raises(TimeoutError):
        c.get(URL)
    assert c.requests == wire.closed == 1
    c, wire = client([Response(content=b'01234567890')], max_bytes=5)
    with pytest.raises(PublicBoardError, match='byte'):
        c.get(URL)
    c, wire = client([Response(404)], max_requests=1)
    with pytest.raises(PublicBoardError, match='budget'):
        c.get(URL)


def test_retry_after_date_and_numeric_contract():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert retry_after_seconds('28800') == 28800
    assert retry_after_seconds('Tue, 20 Oct 2026 08:00:00 GMT', now=now) >= 28800
    assert retry_after_seconds('invalid') == 0


def test_rss_pagination_and_run_limit_are_honest():
    c, wire = client([Response(404), Response(content=feed([{'id': 1}, {'id': 2}])), Response(content=feed([{'id': 3}]))])
    result = fetch_teamtailor_board(CONTRACT, {'page_size': 2, 'max_pages_per_board': 2, 'max_jobs_per_board': 3}, client=c)
    assert result['raw_count'] == result['normalized_count'] == 3
    assert result['requests'] == 3
    assert 'offset=2' in wire.calls[-1][-1]
    assert {j['ats_job_id'] for j in result['jobs']} == {'1', '2', '3'}


def test_repeated_pagination_does_not_inflate_counts():
    c, wire = client([Response(404), Response(content=feed([{}])), Response(content=feed([{}]))])
    result = fetch_teamtailor_board(CONTRACT, {'page_size': 1, 'max_pages_per_board': 2}, client=c)
    assert result['raw_count'] == result['normalized_count'] == 1
    assert result['requests'] == 3


@pytest.mark.parametrize('payload', [b'<html>not RSS</html>', b'<rss>', b'<!DOCTYPE rss [<!ENTITY x "oops">]><rss/>'])
def test_malformed_rss_is_not_verified(payload):
    with pytest.raises(ValueError):
        parse_teamtailor_feed(payload, CONTRACT)


def test_empty_removed_feed_is_valid_but_does_not_prove_live_ingestion():
    result = parse_teamtailor_feed(feed([]), CONTRACT)
    assert result['contract_observed'] is True
    assert result['raw_count'] == result['normalized_count'] == 0


def test_cross_employer_and_missing_url_are_rejected():
    result = parse_teamtailor_feed(feed([{'url': 'https://other.teamtailor.com/jobs/42-analyst'}, {'url': ''}, {}]), CONTRACT)
    assert result['raw_count'] == 3
    assert result['normalized_count'] == 1
    assert sum(result['rejections'].values()) == 2
    with pytest.raises(ValueError, match='employer'):
        parse_teamtailor_feed(feed([{}]).replace(b'<title>Teamtailor', b'<title>Other Employer'), CONTRACT)


def test_rss_canonical_targeting_persistence_dedupe_and_status_ownership(hunter_db, monkeypatch):
    from app import telegram_control_center
    monkeypatch.setattr(telegram_control_center, 'record_job_observation', lambda **k: None)
    jobs = parse_teamtailor_feed(feed([
        {'id': 1, 'locations': [('London', 'United Kingdom'), ('New York', 'United States')]},
        {'id': 1}, {'id': 2, 'locations': [('London', 'United Kingdom')]},
        {'id': 3, 'title': 'Senior People Analytics Analyst'}, {'id': 4, 'title': 'Software Engineer'},
        {'id': 5, 'locations': []},
    ]), CONTRACT)['jobs']
    funnel = filter_dashboard_jobs(jobs)
    assert funnel['eligible'] == 1
    assert funnel['duplicates_within_run'] == 1
    assert funnel['reject_location'] == 2
    assert funnel['reject_hard_requirement'] == 1
    assert funnel['reject_role'] == 1
    assert funnel['accounting_delta'] == 0
    eligible = funnel['eligible_jobs'][0]
    assert eligible['country'] == 'US'
    c = database.get_connection()
    first = save_job(c, eligible, actor='teamtailor_worker'); c.commit()
    assert first['inserted']
    c.execute("UPDATE jobs SET status='already_applied' WHERE id=?", (first['job_id'],)); c.commit()
    second = save_job(c, eligible, actor='teamtailor_worker'); c.commit()
    assert not second['inserted'] and second['job_id'] == first['job_id']
    assert c.execute('SELECT status FROM jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == 'already_applied'
    assert c.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
    c.close()


def jobposting(**changes):
    return {'@type': 'JobPosting', 'title': 'People Analytics Analyst',
            'identifier': {'value': 'REQ-42'}, 'hiringOrganization': {'name': 'Teamtailor'},
            'description': 'Analyze workforce data and create HR dashboards.',
            'url': f'https://{HOST}/jobs/42-analyst', 'jobLocation': {'address': {
                'addressLocality': 'New York', 'addressRegion': 'NY', 'addressCountry': 'US'}}, **changes}


def parse_jsonld(items):
    return parse_jobposting_html(f'https://{HOST}/jobs/', '<script type="application/ld+json">' + json.dumps({'@graph': items}) + '</script>', company='Teamtailor', provider='Fixture ATS', job_path_prefix='/jobs/')


def test_jsonld_normalization_identity_multilocation_and_rejections(hunter_db):
    result = parse_jsonld([
        jobposting(jobLocation=[{'address': {'addressLocality': 'London', 'addressCountry': 'GB'}}, {'address': {'addressLocality': 'New York', 'addressCountry': 'US'}}]),
        jobposting(hiringOrganization={'name': 'Another Employer'}),
        jobposting(url='https://other.example/jobs/42'), jobposting(identifier=None),
        jobposting(validThrough='2000-01-01'), jobposting(url=''),
    ])
    assert result['raw_count'] == 6
    assert result['normalized_count'] == 1
    assert sum(result['rejections'].values()) == 5
    funnel = filter_dashboard_jobs(result['jobs'])
    assert funnel['eligible'] == 1 and funnel['eligible_jobs'][0]['country'] == 'US'


def test_unknown_remote_country_is_never_assumed_us(hunter_db):
    result = parse_jsonld([jobposting(jobLocation=[], jobLocationType='TELECOMMUTE')])
    assert filter_dashboard_jobs(result['jobs'])['eligible'] == 0
    result = parse_jsonld([jobposting(jobLocation=[], jobLocationType='TELECOMMUTE', applicantLocationRequirements={'name': 'United States'})])
    assert filter_dashboard_jobs(result['jobs'])['eligible'] == 1


def test_ashby_missing_country_and_secondary_us_location(hunter_db):
    job = normalize_ashby_job(company_name='Teamtailor', board_name='fixture', raw_job={
        'id': 'abc', 'title': 'People Analytics Analyst', 'location': 'London',
        'jobUrl': 'https://jobs.ashbyhq.com/fixture/abc', 'descriptionPlain': 'Analyze workforce data.',
        'secondaryLocations': [{'location': 'New York, NY', 'address': {'addressCountry': 'USA', 'addressLocality': 'New York', 'addressRegion': 'NY'}}],
    })
    assert job['country'] != 'US'
    result = filter_dashboard_jobs([job])
    assert result['eligible'] == 1 and result['eligible_jobs'][0]['country'] == 'US'


def test_recruitee_429_stops_provider_and_counts_failed_attempt(monkeypatch):
    monkeypatch.setattr(market, '_board_rows', lambda **k: [{'board_locator': 'one', 'company_name': 'One'}, {'board_locator': 'two', 'company_name': 'Two'}])
    calls = []
    def blocked(url):
        calls.append(url)
        response = requests.Response(); response.status_code = 429; response.headers['Retry-After'] = '28800'
        raise requests.HTTPError('fixture limit', response=response)
    monkeypatch.setattr(market, '_request', blocked)
    result = market.fetch_recruitee(Namespace(max_boards=2, max_jobs=10))
    assert len(calls) == result['requests_made'] == 1
    assert result['successful_boards'] == 0
    assert result['errors'][0]['retry_after_seconds'] == 28800


def test_unverified_html_board_never_opens_network(monkeypatch):
    monkeypatch.setattr(market, '_board_rows', lambda **k: [{'board_url': 'https://evil.example/jobs', 'company_name': 'Teamtailor'}])
    result = market.fetch_schema_jobposting(Namespace(max_boards=1, max_jobs=10))
    assert result['configuration_required']
    assert result['requests_made'] == 0


def test_no_store_provider_never_initializes_updates_or_persists(hunter_db, monkeypatch):
    runtime = json.loads(Path('config/provider_runtime_policy.json').read_text())
    database.save_setting('provider_runtime', runtime)
    monkeypatch.setattr(common, '_source_enabled', lambda p: True)
    monkeypatch.setattr(common, '_source_tier', lambda p: 1)
    monkeypatch.setattr(common, '_load_boards', lambda *a: [dict(CONTRACT, id=1)])
    for name in ('initialize_database', '_update_board', '_update_health', 'record_source_metrics', 'save_job'):
        monkeypatch.setattr(common, name, lambda *a, **k: pytest.fail('No-store probe attempted a write'))
    parsed = parse_teamtailor_feed(feed([{}]), CONTRACT)
    result = common.run_provider('Teamtailor', lambda *a: dict(parsed, requests=2), no_store=True)
    assert result['raw'] == result['normalized'] == result['eligible'] == 1
    assert result['inserted'] == 0 and result['no_store']


def test_no_board_is_configuration_required_not_healthy(hunter_db, monkeypatch):
    database.save_setting('provider_runtime', json.loads(Path('config/provider_runtime_policy.json').read_text()))
    monkeypatch.setattr(common, '_source_enabled', lambda p: True)
    monkeypatch.setattr(common, '_source_tier', lambda p: 1)
    monkeypatch.setattr(common, '_load_boards', lambda *a: [])
    monkeypatch.setattr(common, '_update_health', lambda *a, **k: pytest.fail('No board must not promote health'))
    result = common.run_provider('Teamtailor', lambda *a: pytest.fail('No board requested'), no_store=True)
    assert result['configuration_required'] and not result['success']


def test_comeet_invalid_board_quarantine_preserves_notes_and_no_store(hunter_db, monkeypatch):
    from app import dashboard_adapter_sources_v2_3_1 as adapters
    c = database.get_connection()
    c.executescript("CREATE TABLE public_adapter_boards(id INTEGER PRIMARY KEY,source_name TEXT,enabled INTEGER,notes TEXT,updated_at TEXT)")
    c.execute("INSERT INTO public_adapter_boards VALUES(1,'Comeet',1,'original evidence',NULL)")
    c.commit(); c.close()
    monkeypatch.setattr(adapters, 'load_enabled_boards', lambda *a: [{'id': 1, 'company_name': 'Example', 'board_url': 'https://www.comeet.co/careers-api/2.0/company/ex/positions'}])
    monkeypatch.setattr(adapters, '_request_timeout', lambda *a: 1)
    def invalid(*a, **k):
        response = requests.Response(); response.status_code = 400
        raise requests.HTTPError('must-not-log-token', response=response)
    monkeypatch.setattr(adapters, '_request', invalid)
    token = adapters._READ_ONLY_FETCH.set(True)
    try:
        _, errors = adapters.fetch_comeet(adapters.SOURCE_SPECS['comeet'], 10)
    finally:
        adapters._READ_ONLY_FETCH.reset(token)
    c = database.get_connection()
    assert c.execute('SELECT enabled FROM public_adapter_boards WHERE id=1').fetchone()[0] == 1
    c.close()
    assert 'must-not-log-token' not in str(errors)
    adapters.fetch_comeet(adapters.SOURCE_SPECS['comeet'], 10)
    c = database.get_connection()
    row = c.execute('SELECT enabled,notes FROM public_adapter_boards WHERE id=1').fetchone()
    c.close()
    assert row['enabled'] == 0
    assert row['notes'].startswith('original evidence') and 'HTTP 400' in row['notes']


def test_scheduler_backoff_is_at_least_provider_retry_after(hunter_db):
    from app import source_cooldown
    database.save_setting('provider_runtime', json.loads(Path('config/provider_runtime_policy.json').read_text()))
    c = database.get_connection()
    c.execute("INSERT INTO source_health(source_name,source_tier,enabled,cadence_minutes,cost_mode) VALUES('Fixture Recruitee',1,1,60,'free')")
    c.commit(); c.close()
    before = datetime.now(timezone.utc)
    result = source_cooldown.mark_source_completed('Fixture Recruitee', success=False, blocked=True, retry_after_seconds=28800)
    deadline = datetime.fromisoformat(result['next_run_at'])
    assert (deadline - before).total_seconds() >= 28799


@pytest.mark.parametrize('apply,expected', [
    ('https://one.recruitee.com/o/hr-analyst/c/new', 'https://one.recruitee.com/o/hr-analyst/c/new'),
    ('https://two.recruitee.com/o/hr-analyst/c/new', 'https://one.recruitee.com/o/hr-analyst'),
    ('https://one.recruitee.com/o/another-job/c/new', 'https://one.recruitee.com/o/hr-analyst'),
    ('', 'https://one.recruitee.com/o/hr-analyst'),
])
def test_recruitee_preserves_confirmed_dual_links_without_contamination(monkeypatch, apply, expected):
    monkeypatch.setattr(market, '_board_rows', lambda **k: [{'board_locator': 'one', 'company_name': 'One'}])
    class Payload:
        def json(self):
            return {'offers': [{'id': '42', 'title': 'People Analytics Analyst', 'location': 'New York, NY',
                                'careers_url': 'https://one.recruitee.com/o/hr-analyst', 'careers_apply_url': apply}]}
    monkeypatch.setattr(market, '_request', lambda *a: Payload())
    monkeypatch.setattr(market, '_configured_source_tier', lambda *a: 1)
    result = market.fetch_recruitee(Namespace(max_boards=1, max_jobs=10))
    assert result['jobs'][0]['job_url'] == 'https://one.recruitee.com/o/hr-analyst'
    assert result['jobs'][0]['apply_url'] == expected


def test_discovery_change_does_not_change_database_schema_contract():
    import hashlib
    assert hashlib.sha256(database.SCHEMA_SQL.encode()).hexdigest() == '38e0337ae5e9f031b823e8dc11804a792666205cd86d7f643624acf7a73a97d1'


@pytest.mark.parametrize('url', ['https://127.0.0.1/careers', 'https://other.example/careers',
                                'https://user:pass@fixture.bamboohr.com/careers', 'https://fixture.bamboohr.com/employees'])
def test_bamboo_unapproved_board_is_rejected_before_http(url):
    from app.sources.bamboohr import bamboo_list_url
    with pytest.raises(ValueError):
        bamboo_list_url({'tenant': 'fixture', 'board_url': url})


@pytest.mark.parametrize('url', ['http://fixture.wd5.myworkdayjobs.com', 'https://127.0.0.1',
                                'https://user:pass@fixture.wd5.myworkdayjobs.com', 'https://other.example'])
def test_workday_unapproved_board_is_rejected_before_http(url):
    from app.sources.workday import workday_api_base
    with pytest.raises(ValueError):
        workday_api_base({'tenant': 'fixture', 'site_name': 'Careers', 'board_url': url})


def test_bamboo_and_workday_links_cannot_change_employer_or_job():
    from app.sources.bamboohr import normalize_bamboohr_job
    from app.sources.workday import normalize_workday_job
    b = {'tenant': 'fixture', 'company_name': 'Fixture', 'board_url': 'https://fixture.bamboohr.com/careers'}
    for url in ('https://other.bamboohr.com/careers/42', 'https://fixture.bamboohr.com/careers/43'):
        with pytest.raises(ValueError):
            normalize_bamboohr_job(b, {'id': '42', 'url': url})
    with pytest.raises(ValueError, match='different job'):
        normalize_bamboohr_job(b, {'id': '42'}, {'id': '43'})
    w = {'tenant': 'fixture', 'company_name': 'Fixture', 'board_url': 'https://fixture.wd5.myworkdayjobs.com'}
    for url in ('https://other.wd5.myworkdayjobs.com/job/analyst_REQ-1', 'https://fixture.wd5.myworkdayjobs.com/job/analyst_REQ-2'):
        with pytest.raises(ValueError):
            normalize_workday_job(w, {'externalPath': '/job/analyst_REQ-1', 'externalUrl': url})
