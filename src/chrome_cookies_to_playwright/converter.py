"""Cookie conversion, export, and merge logic."""
from __future__ import annotations

import logging
import os

import browser_cookie3

from ._types import CookieDecryptor, InternalCookie
from .chrome import (
    ExportError,
    get_chrome_cookies_db_path,
    list_profiles,
    read_chrome_sqlite_metadata,
)

logger = logging.getLogger(__name__)

# Chrome timestamps use a Windows-style epoch: 1601-01-01 00:00:00 UTC,
# stored in microseconds.  The delta to the Unix epoch is 11644473600 seconds.
CHROME_EPOCH_DELTA = 11644473600

SAMESITE_MAP = {
    -1: "None",
    0: "None",
    1: "Lax",
    2: "Strict",
}


def _default_decryptor(*, cookie_file: str):
    """Default decryptor using browser_cookie3."""
    return browser_cookie3.chrome(cookie_file=cookie_file)


def chrome_timestamp_to_unix(chrome_ts: int) -> float:
    """Convert a Chrome microsecond timestamp to a Unix timestamp (seconds).

    Returns -1 for session cookies (chrome_ts == 0).
    """
    if chrome_ts == 0:
        return -1  # session cookie
    return (chrome_ts / 1_000_000) - CHROME_EPOCH_DELTA


def export_cookies(
    profile: str = "Default",
    domain_filter: str | None = None,
    *,
    decryptor: CookieDecryptor | None = None,
) -> list[InternalCookie]:
    """Export Chrome cookies as a list of Playwright-format cookie dicts.

    Args:
        profile: Chrome profile directory name.
        domain_filter: Only include cookies whose domain contains this string.
        decryptor: Optional callable to decrypt cookies. Defaults to browser_cookie3.chrome.

    Raises:
        ExportError: If the profile cannot be read.
    """
    if decryptor is None:
        decryptor = _default_decryptor

    db_path = get_chrome_cookies_db_path(profile)
    if not os.path.exists(db_path):
        raise ExportError(f"Chrome Cookies database not found: {db_path}")

    # Step 1: Decrypt cookie values via decryptor (default: browser_cookie3 + macOS Keychain)
    logger.info("Decrypting Chrome cookies for profile '%s'...", profile)
    try:
        cookie_jar = decryptor(cookie_file=db_path)
    except Exception as e:
        raise ExportError(
            f"Failed to decrypt Chrome cookies for profile '{profile}': {e}"
        ) from e

    # Build (domain, name, path) -> decrypted value mapping
    decrypted: dict[tuple[str, str, str], dict] = {}
    for cookie in cookie_jar:
        key = (cookie.domain, cookie.name, cookie.path)
        decrypted[key] = {
            "value": cookie.value if cookie.value is not None else "",
            "secure": cookie.secure,
            "expires": cookie.expires,
        }

    logger.info("  browser_cookie3 decrypted %d cookies", len(decrypted))

    # Step 2: Read httpOnly and sameSite metadata from Chrome's SQLite database
    logger.info("Reading Chrome SQLite metadata (httpOnly, sameSite)...")
    metadata = read_chrome_sqlite_metadata(db_path)
    logger.info("  SQLite contains %d cookie records", len(metadata))

    # Step 3: Join the two data sources into Playwright format
    pw_cookies: list[InternalCookie] = []
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
            last_update = meta["last_update_utc"]
        else:
            # Fallback: no SQLite metadata match, use browser_cookie3 values
            logger.warning(
                "No SQLite metadata for cookie %s on %s%s; "
                "falling back to browser_cookie3 values",
                name, domain, path,
            )
            http_only = False
            same_site = "Lax"
            expires = float(dec["expires"]) if dec["expires"] else -1
            secure = dec["secure"]
            last_update = 0

        pw_cookies.append(
            InternalCookie(
                name=name,
                value=dec["value"],
                domain=domain,
                path=path,
                expires=expires,
                httpOnly=http_only,
                secure=secure,
                sameSite=same_site,
                _last_update_utc=last_update,
            )
        )

    logger.info("  Matched metadata for %d/%d cookies", matched, len(pw_cookies))
    return pw_cookies


def export_all_profiles(
    domain_filter: str | None = None,
    *,
    decryptor: CookieDecryptor | None = None,
) -> list[InternalCookie]:
    """Export and merge cookies from all Chrome profiles.

    For duplicate cookies (same domain/name/path), the most recently
    updated cookie wins.
    """
    profiles = list_profiles()
    profiles_with_cookies = [p for p in profiles if p["cookies_exists"]]

    if not profiles_with_cookies:
        raise ExportError("No Chrome profiles with cookies found")

    logger.info(
        "Found %d profile(s) with cookies: %s",
        len(profiles_with_cookies),
        ", ".join(p["dir_name"] for p in profiles_with_cookies),
    )

    merged: dict[tuple[str, str, str], InternalCookie] = {}
    for prof in profiles_with_cookies:
        try:
            cookies = export_cookies(prof["dir_name"], domain_filter, decryptor=decryptor)
        except ExportError as e:
            logger.warning("Skipping profile '%s': %s", prof["dir_name"], e)
            continue
        for cookie in cookies:
            key = (cookie["domain"], cookie["name"], cookie["path"])
            existing = merged.get(key)
            if existing is None or cookie["_last_update_utc"] > existing["_last_update_utc"]:
                merged[key] = cookie

    if not merged:
        raise ExportError(
            "All profiles were skipped due to errors — no cookies exported"
        )

    result = list(merged.values())
    logger.info("Merged total: %d unique cookies across all profiles", len(result))
    return result


def strip_internal_fields(cookies: list[dict]) -> list[dict]:
    """Remove internal fields (prefixed with _) before writing output."""
    return [
        {k: v for k, v in cookie.items() if not k.startswith("_")}
        for cookie in cookies
    ]
