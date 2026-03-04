"""Tests for chrome_cookies_to_playwright.chrome."""
from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from chrome_cookies_to_playwright.chrome import (
    ExportError,
    _validate_db_path,
    check_platform,
    read_chrome_sqlite_metadata,
)


class TestCheckPlatform:
    def test_non_darwin_raises(self):
        with mock.patch("chrome_cookies_to_playwright.chrome.sys") as mock_sys:
            mock_sys.platform = "linux"
            with pytest.raises(ExportError, match="only supports macOS"):
                check_platform()

    def test_darwin_passes(self):
        with mock.patch("chrome_cookies_to_playwright.chrome.sys") as mock_sys:
            mock_sys.platform = "darwin"
            check_platform()  # should not raise


class TestValidateDbPath:
    def test_safe_path(self):
        _validate_db_path("/Users/me/Library/Application Support/Google/Chrome/Default/Cookies")

    def test_unsafe_path_with_single_quote(self):
        with pytest.raises(ExportError, match="Unsafe characters"):
            _validate_db_path("/tmp/evil'; DROP TABLE cookies; --")

    def test_empty_path(self):
        # Empty string doesn't match _SAFE_PATH_RE (requires at least one char)
        with pytest.raises(ExportError, match="Unsafe characters"):
            _validate_db_path("")


class TestReadSqliteMetadataLocked:
    def test_database_locked_raises_export_error(self, tmp_path):
        db_path = str(tmp_path / "Cookies")
        # Create a valid DB so the connect succeeds
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE cookies ("
            "  host_key TEXT, name TEXT, path TEXT, expires_utc INTEGER,"
            "  is_secure INTEGER, is_httponly INTEGER, samesite INTEGER,"
            "  last_update_utc INTEGER)"
        )
        conn.close()

        with mock.patch("chrome_cookies_to_playwright.chrome.sqlite3") as mock_sqlite3:
            mock_conn = mock.MagicMock()
            mock_sqlite3.connect.return_value = mock_conn
            mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
            # Re-import the real OperationalError for the except clause
            mock_sqlite3.OperationalError = sqlite3.OperationalError

            with pytest.raises(ExportError, match="database is locked.*close Chrome"):
                read_chrome_sqlite_metadata(db_path)
