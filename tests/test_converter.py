"""Tests for chrome_cookies_to_playwright.converter."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from unittest import mock

import pytest

from chrome_cookies_to_playwright.chrome import ExportError
from chrome_cookies_to_playwright.converter import (
    CHROME_EPOCH_DELTA,
    chrome_timestamp_to_unix,
    export_all_profiles,
    export_cookies,
    strip_internal_fields,
)
from chrome_cookies_to_playwright.main import main


def _create_test_cookies_db(db_path: str, rows: list[tuple]) -> None:
    """Create a minimal Chrome Cookies SQLite DB for testing."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE cookies ("
        "  host_key TEXT, name TEXT, path TEXT, expires_utc INTEGER,"
        "  is_secure INTEGER, is_httponly INTEGER, samesite INTEGER,"
        "  last_update_utc INTEGER, encrypted_value BLOB, value TEXT"
        ")"
    )
    conn.executemany(
        "INSERT INTO cookies "
        "(host_key, name, path, expires_utc, is_secure, is_httponly, samesite, "
        "last_update_utc, encrypted_value, value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestExportCookiesFallback:
    def test_fallback_logs_warning(self, tmp_path, caplog):
        """When no SQLite metadata matches a decrypted cookie, a warning is logged."""
        db_path = str(tmp_path / "TestProfile" / "Cookies")
        os.makedirs(tmp_path / "TestProfile")
        # Create DB with NO rows so nothing matches
        _create_test_cookies_db(db_path, [])

        fake_cookie = mock.MagicMock()
        fake_cookie.domain = ".example.com"
        fake_cookie.name = "orphan"
        fake_cookie.path = "/"
        fake_cookie.value = "val"
        fake_cookie.secure = False
        fake_cookie.expires = 0

        with (
            mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(tmp_path)),
            mock.patch("chrome_cookies_to_playwright.converter.browser_cookie3") as mock_bc3,
            caplog.at_level(logging.WARNING, logger="chrome_cookies_to_playwright.converter"),
        ):
            mock_bc3.chrome.return_value = [fake_cookie]
            cookies = export_cookies("TestProfile")

        assert len(cookies) == 1
        assert "No SQLite metadata" in caplog.text
        assert "orphan" in caplog.text


class TestExportCookiesCustomDecryptor:
    def test_custom_decryptor_is_used(self, tmp_path):
        """A custom decryptor callable is used instead of browser_cookie3."""
        db_path = str(tmp_path / "TestProfile" / "Cookies")
        os.makedirs(tmp_path / "TestProfile")
        _create_test_cookies_db(db_path, [
            (".example.com", "tok", "/", 0, 1, 0, 0, 10, b"", ""),
        ])

        fake_cookie = mock.MagicMock()
        fake_cookie.domain = ".example.com"
        fake_cookie.name = "tok"
        fake_cookie.path = "/"
        fake_cookie.value = "custom_decrypted"
        fake_cookie.secure = True
        fake_cookie.expires = 0

        custom_decryptor = mock.MagicMock(return_value=[fake_cookie])

        with mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(tmp_path)):
            cookies = export_cookies("TestProfile", decryptor=custom_decryptor)

        custom_decryptor.assert_called_once_with(cookie_file=db_path)
        assert len(cookies) == 1
        assert cookies[0]["value"] == "custom_decrypted"


class TestCookieValueNone:
    def test_none_becomes_empty_string(self, tmp_path):
        """A cookie with value=None gets converted to empty string."""
        db_path = str(tmp_path / "TestProfile" / "Cookies")
        os.makedirs(tmp_path / "TestProfile")
        _create_test_cookies_db(db_path, [
            (".example.com", "nv", "/", 0, 0, 0, 0, 1, b"", ""),
        ])

        fake_cookie = mock.MagicMock()
        fake_cookie.domain = ".example.com"
        fake_cookie.name = "nv"
        fake_cookie.path = "/"
        fake_cookie.value = None  # This is the key: value is None
        fake_cookie.secure = False
        fake_cookie.expires = 0

        with (
            mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(tmp_path)),
            mock.patch("chrome_cookies_to_playwright.converter.browser_cookie3") as mock_bc3,
        ):
            mock_bc3.chrome.return_value = [fake_cookie]
            cookies = export_cookies("TestProfile")

        assert cookies[0]["value"] == ""


class TestNegativeChromeTimestamp:
    def test_small_timestamp_produces_negative_unix(self):
        """A very small Chrome timestamp maps to a negative Unix time (before 1970)."""
        # 1 microsecond from Chrome epoch -> way before Unix epoch
        result = chrome_timestamp_to_unix(1)
        assert result < 0
        assert result == pytest.approx((1 / 1_000_000) - CHROME_EPOCH_DELTA)


class TestMainCLI:
    def test_profile_all_via_cli(self, tmp_path):
        """Running main() with -p all calls export_all_profiles."""
        out_file = str(tmp_path / "out.json")
        cookie = {
            "name": "x", "value": "v", "domain": ".d.com", "path": "/",
            "expires": -1, "httpOnly": False, "secure": False,
            "sameSite": "Lax", "_last_update_utc": 0,
        }

        with (
            mock.patch("sys.argv", ["prog", "-o", out_file, "-p", "all"]),
            mock.patch("chrome_cookies_to_playwright.main.export_all_profiles", return_value=[cookie]),
            mock.patch("chrome_cookies_to_playwright.main.check_platform"),
        ):
            main()

        data = json.loads(open(out_file).read())
        assert len(data["cookies"]) == 1
        assert data["origins"] == []

    def test_output_dir_missing(self, tmp_path, capsys):
        """Nonexistent output directory causes SystemExit(1)."""
        out_file = str(tmp_path / "nonexistent_dir" / "cookies.json")

        with (
            mock.patch("sys.argv", ["prog", "-o", out_file, "-p", "Default"]),
            mock.patch("chrome_cookies_to_playwright.main.check_platform"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Output directory does not exist" in captured.err

    def test_output_json_structure(self, tmp_path):
        """Output file has correct Playwright storage state structure."""
        out_file = str(tmp_path / "state.json")
        cookie = {
            "name": "sid", "value": "abc", "domain": ".example.com",
            "path": "/", "expires": 1700000000.0, "httpOnly": True,
            "secure": True, "sameSite": "Strict", "_last_update_utc": 500,
        }

        with (
            mock.patch("sys.argv", ["prog", "-o", out_file, "-p", "Default"]),
            mock.patch("chrome_cookies_to_playwright.main.export_cookies", return_value=[cookie]),
            mock.patch("chrome_cookies_to_playwright.main.check_platform"),
        ):
            main()

        data = json.loads(open(out_file).read())
        assert "cookies" in data
        assert "origins" in data
        assert isinstance(data["cookies"], list)
        assert isinstance(data["origins"], list)

        c = data["cookies"][0]
        assert set(c.keys()) == {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
        assert c["name"] == "sid"
        assert c["httpOnly"] is True
        assert c["secure"] is True
        assert c["sameSite"] == "Strict"

    def test_platform_check_fails(self, capsys):
        """Non-macOS platform causes SystemExit(1)."""
        with (
            mock.patch("sys.argv", ["prog", "-p", "Default"]),
            mock.patch(
                "chrome_cookies_to_playwright.main.check_platform",
                side_effect=ExportError("only supports macOS"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "only supports macOS" in captured.err
