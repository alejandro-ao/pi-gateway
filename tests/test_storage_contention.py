import sqlite3
from unittest.mock import Mock, patch

import pytest

from pi_gateway.storage import enable_wal


def busy_error(code):
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = code
    return error


def test_wal_initialization_retries_transient_contention():
    conn = Mock()
    conn.execute.side_effect = [
        busy_error(sqlite3.SQLITE_BUSY),
        busy_error(sqlite3.SQLITE_LOCKED),
        None,
    ]
    with patch("pi_gateway.storage.time.sleep") as sleep:
        enable_wal(conn)
    assert conn.execute.call_count == 3
    assert sleep.call_count == 2
    conn.execute.assert_called_with("PRAGMA journal_mode=WAL")


def test_wal_retries_have_a_deadline():
    conn = Mock()
    conn.execute.side_effect = busy_error(sqlite3.SQLITE_BUSY)
    with (
        patch("pi_gateway.storage.time.monotonic", side_effect=[0, 1, 6]),
        patch("pi_gateway.storage.time.sleep") as sleep,
    ):
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            enable_wal(conn)
    assert conn.execute.call_count == 2
    sleep.assert_called_once()


def test_wal_does_not_retry_unrelated_errors():
    conn = Mock()
    error = sqlite3.OperationalError("disk I/O error")
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR
    conn.execute.side_effect = error
    with patch("pi_gateway.storage.time.sleep") as sleep:
        with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
            enable_wal(conn)
    sleep.assert_not_called()
    assert conn.execute.call_count == 1
