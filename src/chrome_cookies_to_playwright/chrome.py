"""Chrome profile discovery and SQLite cookie metadata reading."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import tempfile

from ._types import ChromeSqliteMetadata, ProfileInfo

logger = logging.getLogger(__name__)

CHROME_DATA_DIR = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome"
)

# Only allow printable characters without single quotes in DB paths
# to prevent SQL injection via VACUUM INTO (which requires string interpolation).
_SAFE_PATH_RE = re.compile(r"^[^']+$")


class ExportError(Exception):
    """Raised when cookie export fails for a profile."""


def check_platform() -> None:
    """Raise ExportError if not running on macOS."""
    if sys.platform != "darwin":
        raise ExportError(
            f"This tool only supports macOS, but detected platform: {sys.platform}"
        )


def _validate_db_path(path: str) -> None:
    """Validate that a database path is safe for use in VACUUM INTO.

    Raises ExportError if the path contains characters that could
    allow SQL injection in the interpolated VACUUM INTO statement.
    """
    if not _SAFE_PATH_RE.match(path):
        raise ExportError(
            f"Unsafe characters in database path: {path!r}"
        )


def list_profiles() -> list[ProfileInfo]:
    """Discover all Chrome profiles by reading Local State.

    Returns a list of ProfileInfo dicts with keys: dir_name, display_name,
    cookies_exists.
    """
    local_state_path = os.path.join(CHROME_DATA_DIR, "Local State")
    if not os.path.exists(local_state_path):
        logger.error("Chrome Local State not found: %s", local_state_path)
        return []

    try:
        with open(local_state_path) as f:
            local_state = json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.error("Chrome Local State is corrupted: %s", local_state_path)
        return []

    info_cache = local_state.get("profile", {}).get("info_cache", {})
    profiles: list[ProfileInfo] = []
    for dir_name, info in info_cache.items():
        cookies_path = os.path.join(CHROME_DATA_DIR, dir_name, "Cookies")
        profiles.append(
            ProfileInfo(
                dir_name=dir_name,
                display_name=info.get("name", dir_name),
                cookies_exists=os.path.exists(cookies_path),
            )
        )

    # Fallback: if info_cache is empty (fresh install), check for Default profile
    known_dirs = {p["dir_name"] for p in profiles}
    if "Default" not in known_dirs:
        default_cookies = os.path.join(CHROME_DATA_DIR, "Default", "Cookies")
        if os.path.exists(default_cookies):
            profiles.append(
                ProfileInfo(
                    dir_name="Default",
                    display_name="Default",
                    cookies_exists=True,
                )
            )

    return profiles


def get_chrome_cookies_db_path(profile: str = "Default") -> str:
    """Return the path to Chrome's Cookies SQLite database for the given profile."""
    return os.path.join(CHROME_DATA_DIR, profile, "Cookies")


def read_chrome_sqlite_metadata(db_path: str) -> dict[tuple[str, str, str], ChromeSqliteMetadata]:
    """Read Chrome's Cookies SQLite and return metadata keyed by (host_key, name, path).

    Uses VACUUM INTO for a consistent snapshot that includes WAL journal data.
    """
    fd, tmp_db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(tmp_db)  # VACUUM INTO needs a non-existent target

    _validate_db_path(db_path)
    _validate_db_path(tmp_db)

    try:
        # VACUUM INTO produces a consistent snapshot including WAL data,
        # unlike a simple file copy which misses Cookies-wal/Cookies-shm.
        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            src_conn.execute(f"VACUUM INTO '{tmp_db}'")
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                raise ExportError(
                    f"Chrome Cookies database is locked: {db_path}. "
                    "Hint: close Chrome or wait for it to release the lock."
                ) from e
            raise
        finally:
            src_conn.close()

        conn = sqlite3.connect(tmp_db)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT host_key, name, path, expires_utc, is_secure, "
                "is_httponly, samesite, last_update_utc "
                "FROM cookies"
            ).fetchall()
        except sqlite3.OperationalError as e:
            raise ExportError(
                f"Chrome Cookies DB schema unsupported (is Chrome too new?): {e}"
            ) from e
        finally:
            conn.close()
    finally:
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)

    metadata: dict[tuple[str, str, str], ChromeSqliteMetadata] = {}
    for row in rows:
        key = (row["host_key"], row["name"], row["path"])
        metadata[key] = ChromeSqliteMetadata(
            expires_utc=row["expires_utc"],
            is_secure=bool(row["is_secure"]),
            is_httponly=bool(row["is_httponly"]),
            samesite=row["samesite"],
            last_update_utc=row["last_update_utc"],
        )
    return metadata
