#!/usr/bin/env python3
"""CLI entry point for chrome-cookies-to-playwright."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from .chrome import ExportError, check_platform, list_profiles
from .converter import export_all_profiles, export_cookies, strip_internal_fields


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
        default="all",
        help='Chrome profile directory name, or "all" to merge all profiles (default: "all")',
    )
    parser.add_argument(
        "--domain",
        "-d",
        default=None,
        help="only export cookies whose domain contains this string",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="list discovered Chrome profiles and exit",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="enable verbose logging output",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    try:
        check_platform()
    except ExportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.list_profiles:
        profiles = list_profiles()
        if not profiles:
            print("No Chrome profiles found.")
            sys.exit(1)
        print(f"{'Directory':<20} {'Display Name':<30} {'Cookies DB'}")
        print("-" * 65)
        for p in profiles:
            status = "found" if p["cookies_exists"] else "missing"
            print(f"{p['dir_name']:<20} {p['display_name']:<30} {status}")
        sys.exit(0)

    # Validate output directory exists before doing expensive work
    output_dir = os.path.dirname(args.output)
    if output_dir and not os.path.isdir(output_dir):
        print(f"Error: Output directory does not exist: {output_dir}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.profile == "all":
            cookies = export_all_profiles(args.domain)
        else:
            cookies = export_cookies(args.profile, args.domain)
    except ExportError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(
            "Hint: Make sure your terminal has 'Full Disk Access' permission "
            "(System Settings > Privacy & Security > Full Disk Access).",
            file=sys.stderr,
        )
        sys.exit(1)

    cookies = strip_internal_fields(cookies)

    state = {
        "cookies": cookies,
        "origins": [],
    }

    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nExported {len(cookies)} cookies to {args.output}")


if __name__ == "__main__":
    main()
