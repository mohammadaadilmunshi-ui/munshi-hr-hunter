from __future__ import annotations

import json
import sqlite3

import requests

from app import employer_board_discovery as discovery


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _http_error(status_code: int) -> requests.HTTPError:
    return requests.HTTPError(
        f"HTTP {status_code}",
        response=_Response(status_code),
    )


def test_commoncrawl_404_is_empty_pattern_not_source_error(monkeypatch) -> None:
    calls = []

    def fake_request(*_args, **_kwargs):
        calls.append(1)
        raise _http_error(404)

    monkeypatch.setattr(discovery, "_request_limited", fake_request)

    rows = discovery.query_commoncrawl_pattern(
        requests.Session(),
        api_url="https://index.example.test",
        pattern="*.example.test/*",
        page=0,
        page_size=100,
    )

    assert rows == []
    assert len(calls) == 1


def test_commoncrawl_transient_504_retries_then_succeeds(monkeypatch) -> None:
    calls = []

    def fake_request(*_args, **_kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(504)
        body = (json.dumps({"url": "https://jobs.example.test/1"}) + "\n").encode()
        return _Response(200), body

    monkeypatch.setattr(discovery, "_request_limited", fake_request)
    monkeypatch.setattr(discovery.time, "sleep", lambda *_args, **_kwargs: None)

    rows = discovery.query_commoncrawl_pattern(
        requests.Session(),
        api_url="https://index.example.test",
        pattern="*.example.test/*",
        page=0,
        page_size=100,
    )

    assert rows == [{"url": "https://jobs.example.test/1"}]
    assert len(calls) == 3


def test_local_discovery_skips_disabled_board_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """
            CREATE TABLE public_adapter_boards(
                company_name TEXT,
                board_url TEXT,
                enabled INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO public_adapter_boards(company_name,board_url,enabled) VALUES(?,?,?)",
            [
                ("Active", "https://active.recruitee.com/api/offers/", 1),
                ("Quarantined", "https://quarantined.recruitee.com/api/offers/", 0),
            ],
        )

        rows = list(
            discovery._iter_table_urls(
                connection,
                "public_adapter_boards",
            )
        )
    finally:
        connection.close()

    assert rows == [
        ("Active", "https://active.recruitee.com/api/offers/"),
    ]
