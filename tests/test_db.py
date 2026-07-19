"""
api/db.py: the pooled-connection lifecycle. No real DB -- a fake pool records
getconn/putconn and a fake connection records rollback, so we can assert the two
guarantees no other test exercises:

  * connection() always returns the borrowed connection to the pool (no leak), and
  * it rolls back the (read-only) transaction on the way out -- on BOTH the success
    path and the exception path -- so the next borrower gets a clean connection.

Also pins _require_pool()'s clear error when the pool can't be created, rather than
a None dereference deep in a request.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from api import db as apidb  # noqa: E402


class _FakeConn:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()
        self.got = 0
        self.put = []

    def getconn(self):
        self.got += 1
        return self.conn

    def putconn(self, conn):
        self.put.append(conn)


@pytest.fixture
def fake_pool(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(apidb, "_pool", pool)
    return pool


def test_connection_borrows_rolls_back_and_returns(fake_pool):
    with apidb.connection() as conn:
        assert conn is fake_pool.conn
    # borrowed once, rolled back once (clean read-only handback), returned once.
    assert fake_pool.got == 1
    assert fake_pool.conn.rollbacks == 1
    assert fake_pool.put == [fake_pool.conn]


def test_connection_rolls_back_and_returns_on_exception(fake_pool):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with apidb.connection():
            raise Boom()
    # even on error: rolled back and returned to the pool (no connection leak).
    assert fake_pool.conn.rollbacks == 1
    assert fake_pool.put == [fake_pool.conn]


def test_require_pool_raises_when_unavailable(monkeypatch):
    # No pool, and init_pool can't create one (DB down) -> a clear RuntimeError, not
    # a None dereference at the first cursor.
    monkeypatch.setattr(apidb, "_pool", None)
    monkeypatch.setattr(apidb, "init_pool", lambda: None)
    with pytest.raises(RuntimeError, match="database is unavailable"):
        apidb._require_pool()
