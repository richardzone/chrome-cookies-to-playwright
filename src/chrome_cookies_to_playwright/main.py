#!/usr/bin/env python3
"""
Export macOS Chrome cookies to Playwright storage state format.

Combines browser_cookie3 (for decrypted cookie values via Keychain) with
a direct SQLite read of Chrome's Cookies database (for httpOnly, sameSite,
and precise expiry metadata that browser_cookie3 doesn't expose).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

import browser_cookie3

# Chrome timestamps use a Windows-style epoch: 1601-01-01 00:00:00 UTC,
# stored in microseconds.  The delta to the Unix epoch is 11644473600 seconds.
CHROME_EPOCH_DELTA = 11644473600

SAMESITE_MAP = {
    -1: "None",
    0: "None",
    1: "Lax",
    2: "Strict",
}


def chrome_timestamp_to_unix(chrome_ts: int) -> float:
    """Convert a Chrome microsecond timestamp to a Unix timestamp (seconds).

    Returns -1 for session cookies (chrome_ts == 0).
    """
    if chrome_ts == 0:
        return -1  # session cookie
    return (chrome_ts / 1_000_000) - CHROME_EPOCH_DELTA


def get_chrome_cookies_db_path(profile: str = "Default") -> str:
    """Return the path to Chrome's Cookies SQLite database for the given profile."""
    return os.path.expanduser(
        f"~/Library/Application Support/Google/Chrome/{profile}/Cookies"
    )


def read_chrome_sqlite_metadata(db_path: str) -> dict:
    """Read Chrome's Cookies SQLite and return metadata keyed by (host_key, name, path)."""
    tmp_db = os.path.join(tempfile.gettempdir(), "chrome_cookies_export.db")
    shutil.copy2(db_path, tmp_db)

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT host_key, name, path, expires_utc, is_secure, is_httponly, samesite "
        "FROM cookies"
    ).fetchall()
    conn.close()
    os.unlink(tmp_db)

    metadata = {}
    for row in rows:
        key = (row["host_key"], row["name"], row["path"])
        metadata[key] = {
            "expires_utc": row["expires_utc"],
            "is_secure": bool(row["is_secure"]),
            "is_httponly": bool(row["is_httponly"]),
            "samesite": row["samesite"],
        }
    return metadata


def export_cookies(
    profile: str = "Default", domain_filter: str | None = None
) -> list[dict]:
    """Export Chrome cookies as a list of Playwright-format cookie dicts."""
    db_path = get_chrome_cookies_db_path(profile)
    if not os.path.exists(db_path):
        print(f"Error: Chrome Cookies database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Step 1: Decrypt cookie values via browser_cookie3 + macOS Keychain
    print("Decrypting Chrome cookies via browser_cookie3...")
    try:
        cookie_jar = browser_cookie3.chrome(
            cookie_file=db_path if profile != "Default" else None
        )
    except Exception as e:
        print(f"Error: Failed to decrypt Chrome cookies: {e}", file=sys.stderr)
        print(
            "Hint: Make sure your terminal has 'Full Disk Access' permission "
            "(System Settings > Privacy & Security > Full Disk Access).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build (domain, name, path) -> decrypted value mapping
    decrypted = {}
    for cookie in cookie_jar:
        key = (cookie.domain, cookie.name, cookie.path)
        decrypted[key] = {
            "value": cookie.value,
            "secure": cookie.secure,
            "expires": cookie.expires,
        }

    print(f"  browser_cookie3 decrypted {len(decrypted)} cookies")

    # Step 2: Read httpOnly and sameSite metadata from Chrome's SQLite database
    print("Reading Chrome SQLite metadata (httpOnly, sameSite)...")
    metadata = read_chrome_sqlite_metadata(db_path)
    print(f"  SQLite contains {len(metadata)} cookie records")

    # Step 3: Join the two data sources into Playwright format
    pw_cookies = []
    matched = 0
    for (domain, name, path), dec in decrypted.items():
        # Apply domain filter if specified
        if domain_filter and domain_filter not in domain:
            continue

        meta = metadata.get((domain, name, path))

        if meta:
            matched += 1
            http_only = meta["is_httponly"]
            same_site = SAMESITE_MAP.get(meta["samesite"], "None")
            expires = chrome_timestamp_to_unix(meta["expires_utc"])
            secure = meta["is_secure"]
        else:
            # Fallback: no SQLite metadata match, use browser_cookie3 values
            http_only = False
            same_site = "Lax"
            expires = float(dec["expires"]) if dec["expires"] else -1
            secure = dec["secure"]

        pw_cookies.append(
            {
                "name": name,
                "value": dec["value"] or "",
                "domain": domain,
                "path": path,
                "expires": expires,
                "httpOnly": http_only,
                "secure": secure,
                "sameSite": same_site,
            }
        )

    print(f"  Matched metadata for {matched}/{len(pw_cookies)} cookies")
    return pw_cookies


def main():
    parser = argparse.ArgumentParser(
        description="Export Chrome cookies to Playwright storage state format (macOS)"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="/tmp/chrome-cookies-state.json",
        help="output file path (default: /tmp/chrome-cookies-state.json)",
    )
    parser.add_argument(
        "--profile",
        "-p",
        default="Default",
        help='Chrome profile directory name (default: "Default")',
    )
    parser.add_argument(
        "--domain",
        "-d",
        default=None,
        help="only export cookies whose domain contains this string",
    )
    args = parser.parse_args()

    cookies = export_cookies(args.profile, args.domain)

    state = {
        "cookies": cookies,
        "origins": [],
    }

    with open(args.output, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nExported {len(cookies)} cookies to {args.output}")


if __name__ == "__main__":
    main()
