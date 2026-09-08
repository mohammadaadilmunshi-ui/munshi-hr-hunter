from __future__ import annotations

import inspect

from app import n8n_dispatch


def test_queue_candidates_closes_implicit_transaction_before_begin_immediate() -> None:
    source = inspect.getsource(n8n_dispatch.queue_candidates)
    guard = "if connection.in_transaction:"
    commit = "connection.commit()"
    begin = '"BEGIN IMMEDIATE"'

    assert guard in source
    assert source.index(guard) < source.index(begin)
    guard_tail = source[source.index(guard):source.index(begin)]
    assert commit in guard_tail
