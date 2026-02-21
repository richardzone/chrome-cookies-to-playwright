# chrome-cookies-to-playwright

Export your macOS Chrome cookies to [Playwright](https://playwright.dev/) storage state format — with full `httpOnly`, `sameSite`, and expiry metadata.

## Quick Start

```bash
# Zero-install (requires Python 3.9+)
uvx chrome-cookies-to-playwright

# Or install globally
pip install chrome-cookies-to-playwright
chrome-cookies-to-playwright
```

## What It Does

Playwright's built-in cookie APIs cannot access `httpOnly` or `sameSite` flags from a real browser profile. This tool works around that by:

1. Using [browser-cookie3](https://github.com/borisbabic/browser_cookie3) to **decrypt** Chrome's cookie values via the macOS Keychain.
2. Reading Chrome's **SQLite Cookies database** directly to extract `httpOnly`, `sameSite`, `secure`, and precise expiry metadata.
3. Joining the two data sources into a single **Playwright storage state JSON** file that you can load with `browserContext.addCookies()` or the Playwright CLI.

The result is a complete, accurate cookie export that preserves all the metadata Playwright needs.

## Usage

```
chrome-cookies-to-playwright [--output FILE] [--profile NAME] [--domain FILTER]
```

| Option | Description |
|---|---|
| `--output`, `-o` | Output file path (default: `/tmp/chrome-cookies-state.json`) |
| `--profile`, `-p` | Chrome profile directory name (default: `Default`) |
| `--domain`, `-d` | Only export cookies whose domain contains this string |

### Examples

```bash
# Export all cookies
chrome-cookies-to-playwright

# Export only GitHub cookies
chrome-cookies-to-playwright --domain github.com

# Use a specific Chrome profile and custom output path
chrome-cookies-to-playwright --profile "Profile 1" --output ./cookies.json
```

### Using the output with Playwright

```python
# Python
context = browser.new_context(storage_state="/tmp/chrome-cookies-state.json")
```

```javascript
// JavaScript
const context = await browser.newContext({
  storageState: '/tmp/chrome-cookies-state.json'
});
```

## How It Works

Chrome stores cookies in an SQLite database at:
```
~/Library/Application Support/Google/Chrome/<Profile>/Cookies
```

Cookie *values* are encrypted with a key stored in the macOS Keychain. `browser-cookie3` handles this decryption. However, it doesn't expose `httpOnly` or `sameSite` metadata.

This tool reads the SQLite database directly to get those fields, then joins the results with the decrypted values to produce a complete Playwright-compatible storage state.

### Chrome timestamp conversion

Chrome uses a custom epoch (1601-01-01 00:00:00 UTC) with microsecond precision. The tool converts these to Unix timestamps that Playwright expects.

## Requirements

- **macOS** (relies on Chrome's Keychain-based cookie encryption)
- **Google Chrome** installed
- **Full Disk Access** permission for your terminal (System Settings → Privacy & Security → Full Disk Access)
- **Python 3.9+**

## License

MIT
