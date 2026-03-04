"""Type definitions for chrome-cookies-to-playwright."""
from __future__ import annotations

import http.cookiejar
from typing import Protocol, TypedDict


class ChromeSqliteMetadata(TypedDict):
    """Metadata for a single cookie row from Chrome's SQLite database."""

    expires_utc: int
    is_secure: bool
    is_httponly: bool
    samesite: int
    last_update_utc: int


class PlaywrightCookie(TypedDict):
    """A cookie in Playwright storage state format."""

    name: str
    value: str
    domain: str
    path: str
    expires: float
    httpOnly: bool
    secure: bool
    sameSite: str


class InternalCookie(PlaywrightCookie, total=False):
    """PlaywrightCookie with optional internal tracking fields."""

    _last_update_utc: int


class ProfileInfo(TypedDict):
    """Discovered Chrome profile information."""

    dir_name: str
    display_name: str
    cookies_exists: bool


class CookieDecryptor(Protocol):
    """Protocol for a callable that decrypts Chrome cookies."""

    def __call__(self, *, cookie_file: str) -> http.cookiejar.CookieJar: ...
