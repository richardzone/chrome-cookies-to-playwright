"""Tests for chrome_cookies_to_playwright.main (CLI) and converter helpers."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from unittest import mock

import pytest

from chrome_cookies_to_playwright.chrome import (
    CHROME_DATA_DIR,
    ExportError,
    get_chrome_cookies_db_path,
    list_profiles,
    read_chrome_sqlite_metadata,
)
from chrome_cookies_to_playwright.converter import (
    CHROME_EPOCH_DELTA,
    SAMESITE_MAP,
    chrome_timestamp_to_unix,
    export_all_profiles,
    export_cookies,
    strip_internal_fields,
)
from chrome_cookies_to_playwright.main import main


# ---------------------------------------------------------------------------
# chrome_timestamp_to_unix
# ---------------------------------------------------------------------------

class TestChromeTimestampToUnix:
    def test_session_cookie(self):
        assert chrome_timestamp_to_unix(0) == -1

    def test_known_timestamp(self):
        # 13370000000000000 us from Chrome epoch
        chrome_ts = 13370000000000000
        expected = (chrome_ts / 1_000_000) - CHROME_EPOCH_DELTA
        assert chrome_timestamp_to_unix(chrome_ts) == expected

    def test_unix_epoch(self):
        # Chrome timestamp corresponding to Unix epoch (1970-01-01)
        chrome_ts = CHROME_EPOCH_DELTA * 1_000_000
        assert chrome_timestamp_to_unix(chrome_ts) == 0.0


# ---------------------------------------------------------------------------
# SAMESITE_MAP
# ---------------------------------------------------------------------------

class TestSameSiteMap:
    def test_all_values(self):
        assert SAMESITE_MAP[-1] == "None"
        assert SAMESITE_MAP[0] == "None"
        assert SAMESITE_MAP[1] == "Lax"
        assert SAMESITE_MAP[2] == "Strict"

    def test_unknown_falls_back(self):
        assert SAMESITE_MAP.get(99, "None") == "None"


# ---------------------------------------------------------------------------
# strip_internal_fields
# ---------------------------------------------------------------------------

class TestStripInternalFields:
    def test_removes_underscore_fields(self):
        cookies = [
            {"name": "a", "value": "1", "_last_update_utc": 123},
            {"name": "b", "_secret": "x", "domain": ".example.com"},
        ]
        result = strip_internal_fields(cookies)
        assert result == [
            {"name": "a", "value": "1"},
            {"name": "b", "domain": ".example.com"},
        ]

    def test_empty_list(self):
        assert strip_internal_fields([]) == []

    def test_no_internal_fields(self):
        cookies = [{"name": "a", "value": "1"}]
        assert strip_internal_fields(cookies) == cookies


# ---------------------------------------------------------------------------
# get_chrome_cookies_db_path
# ---------------------------------------------------------------------------

class TestGetChromeCookiesDbPath:
    def test_default_profile(self):
        path = get_chrome_cookies_db_path("Default")
        assert path.endswith("/Default/Cookies")
        assert "Google/Chrome" in path

    def test_custom_profile(self):
        path = get_chrome_cookies_db_path("Profile 1")
        assert path.endswith("/Profile 1/Cookies")


# ---------------------------------------------------------------------------
# read_chrome_sqlite_metadata (with real temp SQLite DB)
# ---------------------------------------------------------------------------

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


class TestReadChromeSqliteMetadata:
    def test_reads_metadata(self, tmp_path):
        db_path = str(tmp_path / "Cookies")
        _create_test_cookies_db(db_path, [
            (".example.com", "sid", "/", 13370000000000000, 1, 1, 1, 100, b"", "val"),
            (".other.com", "tok", "/api", 0, 0, 0, 0, 50, b"", "v2"),
        ])
        meta = read_chrome_sqlite_metadata(db_path)
        assert len(meta) == 2

        key1 = (".example.com", "sid", "/")
        assert meta[key1]["is_secure"] is True
        assert meta[key1]["is_httponly"] is True
        assert meta[key1]["samesite"] == 1
        assert meta[key1]["last_update_utc"] == 100

        key2 = (".other.com", "tok", "/api")
        assert meta[key2]["is_secure"] is False
        assert meta[key2]["is_httponly"] is False

    def test_empty_db(self, tmp_path):
        db_path = str(tmp_path / "Cookies")
        _create_test_cookies_db(db_path, [])
        meta = read_chrome_sqlite_metadata(db_path)
        assert meta == {}

    def test_schema_mismatch_raises_export_error(self, tmp_path):
        db_path = str(tmp_path / "Cookies")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE cookies (id INTEGER)")
        conn.close()
        with pytest.raises(ExportError, match="schema unsupported"):
            read_chrome_sqlite_metadata(db_path)

    def test_temp_file_cleaned_up(self, tmp_path):
        db_path = str(tmp_path / "Cookies")
        _create_test_cookies_db(db_path, [])
        before = set(os.listdir(tempfile.gettempdir()))
        read_chrome_sqlite_metadata(db_path)
        after = set(os.listdir(tempfile.gettempdir()))
        # No new .db files should remain
        new_db_files = {f for f in (after - before) if f.endswith(".db")}
        assert new_db_files == set()


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    def test_reads_local_state(self, tmp_path):
        chrome_dir = tmp_path / "Chrome"
        chrome_dir.mkdir()
        # Create Local State
        local_state = {
            "profile": {
                "info_cache": {
                    "Default": {"name": "Person 1"},
                    "Profile 1": {"name": "Work"},
                }
            }
        }
        (chrome_dir / "Local State").write_text(json.dumps(local_state))
        # Create Cookies files
        (chrome_dir / "Default").mkdir()
        (chrome_dir / "Default" / "Cookies").touch()
        (chrome_dir / "Profile 1").mkdir()
        # Profile 1 has no Cookies file

        with mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(chrome_dir)):
            profiles = list_profiles()

        assert len(profiles) == 2
        default = next(p for p in profiles if p["dir_name"] == "Default")
        assert default["display_name"] == "Person 1"
        assert default["cookies_exists"] is True

        p1 = next(p for p in profiles if p["dir_name"] == "Profile 1")
        assert p1["cookies_exists"] is False

    def test_fallback_default_profile(self, tmp_path):
        chrome_dir = tmp_path / "Chrome"
        chrome_dir.mkdir()
        # Local State with empty info_cache
        (chrome_dir / "Local State").write_text(json.dumps({"profile": {"info_cache": {}}}))
        (chrome_dir / "Default").mkdir()
        (chrome_dir / "Default" / "Cookies").touch()

        with mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(chrome_dir)):
            profiles = list_profiles()

        assert len(profiles) == 1
        assert profiles[0]["dir_name"] == "Default"

    def test_missing_local_state(self, tmp_path):
        with mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(tmp_path)):
            profiles = list_profiles()
        assert profiles == []

    def test_corrupted_local_state(self, tmp_path):
        chrome_dir = tmp_path / "Chrome"
        chrome_dir.mkdir()
        (chrome_dir / "Local State").write_text("{bad json")

        with mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(chrome_dir)):
            profiles = list_profiles()
        assert profiles == []


# ---------------------------------------------------------------------------
# export_cookies (mocked browser_cookie3)
# ---------------------------------------------------------------------------

class TestExportCookies:
    def test_missing_db_raises(self):
        with pytest.raises(ExportError, match="not found"):
            export_cookies("nonexistent_profile_xyz")

    def test_joins_decrypted_with_metadata(self, tmp_path):
        db_path = str(tmp_path / "TestProfile" / "Cookies")
        os.makedirs(tmp_path / "TestProfile")
        _create_test_cookies_db(db_path, [
            (".example.com", "sid", "/", 13370000000000000, 1, 1, 2, 999, b"", ""),
        ])

        # Mock browser_cookie3 to return matching cookie
        fake_cookie = mock.MagicMock()
        fake_cookie.domain = ".example.com"
        fake_cookie.name = "sid"
        fake_cookie.path = "/"
        fake_cookie.value = "secret123"
        fake_cookie.secure = True
        fake_cookie.expires = 0

        with (
            mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(tmp_path)),
            mock.patch("chrome_cookies_to_playwright.converter.browser_cookie3") as mock_bc3,
        ):
            mock_bc3.chrome.return_value = [fake_cookie]
            cookies = export_cookies("TestProfile")

        assert len(cookies) == 1
        c = cookies[0]
        assert c["name"] == "sid"
        assert c["value"] == "secret123"
        assert c["httpOnly"] is True
        assert c["sameSite"] == "Strict"
        assert c["secure"] is True
        assert c["_last_update_utc"] == 999

    def test_domain_filter(self, tmp_path):
        db_path = str(tmp_path / "TestProfile" / "Cookies")
        os.makedirs(tmp_path / "TestProfile")
        _create_test_cookies_db(db_path, [
            (".example.com", "a", "/", 0, 0, 0, 0, 1, b"", ""),
            (".other.com", "b", "/", 0, 0, 0, 0, 2, b"", ""),
        ])

        fake_cookies = []
        for domain, name in [(".example.com", "a"), (".other.com", "b")]:
            c = mock.MagicMock()
            c.domain = domain
            c.name = name
            c.path = "/"
            c.value = "v"
            c.secure = False
            c.expires = 0
            fake_cookies.append(c)

        with (
            mock.patch("chrome_cookies_to_playwright.chrome.CHROME_DATA_DIR", str(tmp_path)),
            mock.patch("chrome_cookies_to_playwright.converter.browser_cookie3") as mock_bc3,
        ):
            mock_bc3.chrome.return_value = fake_cookies
            cookies = export_cookies("TestProfile", domain_filter="example")

        assert len(cookies) == 1
        assert cookies[0]["domain"] == ".example.com"


# ---------------------------------------------------------------------------
# export_all_profiles
# ---------------------------------------------------------------------------

class TestExportAllProfiles:
    def test_no_profiles_raises(self):
        with mock.patch("chrome_cookies_to_playwright.converter.list_profiles", return_value=[]):
            with pytest.raises(ExportError, match="No Chrome profiles"):
                export_all_profiles()

    def test_all_profiles_fail_raises(self):
        profiles = [
            {"dir_name": "P1", "display_name": "P1", "cookies_exists": True},
        ]
        with (
            mock.patch("chrome_cookies_to_playwright.converter.list_profiles", return_value=profiles),
            mock.patch(
                "chrome_cookies_to_playwright.converter.export_cookies",
                side_effect=ExportError("fail"),
            ),
        ):
            with pytest.raises(ExportError, match="All profiles were skipped"):
                export_all_profiles()

    def test_merge_keeps_newest(self):
        cookie_old = {
            "name": "sid", "value": "old", "domain": ".x.com", "path": "/",
            "expires": -1, "httpOnly": False, "secure": False,
            "sameSite": "Lax", "_last_update_utc": 100,
        }
        cookie_new = {
            "name": "sid", "value": "new", "domain": ".x.com", "path": "/",
            "expires": -1, "httpOnly": True, "secure": True,
            "sameSite": "Strict", "_last_update_utc": 200,
        }

        profiles = [
            {"dir_name": "P1", "display_name": "P1", "cookies_exists": True},
            {"dir_name": "P2", "display_name": "P2", "cookies_exists": True},
        ]

        def fake_export(profile, domain_filter=None, *, decryptor=None):
            return [cookie_old] if profile == "P1" else [cookie_new]

        with (
            mock.patch("chrome_cookies_to_playwright.converter.list_profiles", return_value=profiles),
            mock.patch("chrome_cookies_to_playwright.converter.export_cookies", side_effect=fake_export),
        ):
            result = export_all_profiles()

        assert len(result) == 1
        assert result[0]["value"] == "new"
        assert result[0]["_last_update_utc"] == 200


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

class TestMain:
    def test_output_file_permissions(self, tmp_path):
        out_file = str(tmp_path / "cookies.json")
        cookie = {
            "name": "x", "value": "v", "domain": ".d.com", "path": "/",
            "expires": -1, "httpOnly": False, "secure": False,
            "sameSite": "Lax", "_last_update_utc": 0,
        }

        with (
            mock.patch("sys.argv", ["prog", "-o", out_file, "-p", "Test"]),
            mock.patch("chrome_cookies_to_playwright.main.export_cookies", return_value=[cookie]),
            mock.patch("chrome_cookies_to_playwright.main.check_platform"),
        ):
            main()

        mode = os.stat(out_file).st_mode
        assert mode & 0o777 == 0o600

        data = json.loads(open(out_file).read())
        assert len(data["cookies"]) == 1
        assert "_last_update_utc" not in data["cookies"][0]

    def test_list_profiles_flag(self, tmp_path):
        profiles = [
            {"dir_name": "Default", "display_name": "Me", "cookies_exists": True},
        ]
        with (
            mock.patch("sys.argv", ["prog", "--list-profiles"]),
            mock.patch("chrome_cookies_to_playwright.main.list_profiles", return_value=profiles),
            mock.patch("chrome_cookies_to_playwright.main.check_platform"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0

    def test_export_error_shows_hint(self, capsys):
        with (
            mock.patch("sys.argv", ["prog", "-p", "BadProfile"]),
            mock.patch(
                "chrome_cookies_to_playwright.main.export_cookies",
                side_effect=ExportError("db not found"),
            ),
            mock.patch("chrome_cookies_to_playwright.main.check_platform"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Full Disk Access" in captured.err
