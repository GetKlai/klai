#!/usr/bin/env python3
"""Validate that the given cookies authenticate against a URL.

Compares the HTTP response WITH cookies vs WITHOUT cookies on the SAME URL.
If the two responses differ measurably (word count, byte size), the cookies
have an effect on the server's behaviour — they authenticate.  If the two
responses are identical, the cookies do nothing and any "Logged in — cookies
verified" UI is a lie.

This is the validation logic the wizard auth-probe should use instead of its
current pattern-match-on-response heuristic, which says auth_ok whenever the
response has >50 words and no auth-wall markers — a check that gives a green
light even if the cookies were stripped on the wire.

Usage:
  python validate-cookies.py <url> 'name=value' ['name=value'...]

Example:
  python validate-cookies.py \\
    'https://wiki.redcactus.cloud/nl/43-bubble-desktop-pop-up' \\
    'prod-knowledgebase-session=eyJ...0%3D' \\
    'XSRF-TOKEN=eyJ...0%3D'

Exit 0 = AUTHENTICATED (cookies measurably change the response).
Exit 1 = NOT AUTHENTICATED (cookies have no effect).
"""
import sys
import httpx

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch(url: str, cookies: dict[str, str]) -> tuple[int, int, int]:
    """Return (status, word_count, byte_size) for a GET on url with cookies."""
    with httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": UA},
    ) as c:
        r = c.get(url, cookies=cookies)
        return r.status_code, len(r.text.split()), len(r.text)


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write(__doc__)
        return 2

    url = sys.argv[1]
    cookies: dict[str, str] = {}
    for arg in sys.argv[2:]:
        if "=" not in arg:
            sys.stderr.write(f"Skip arg without '=': {arg!r}\n")
            continue
        name, _, value = arg.partition("=")
        cookies[name] = value

    print(f"URL:              {url}")
    print(f"Cookies provided: {sorted(cookies.keys())}")
    print()

    a_status, a_words, a_bytes = fetch(url, cookies)
    print(f"WITH cookies:     status={a_status:3d}  words={a_words:6d}  bytes={a_bytes:8d}")

    b_status, b_words, b_bytes = fetch(url, {})
    print(f"WITHOUT cookies:  status={b_status:3d}  words={b_words:6d}  bytes={b_bytes:8d}")
    print()

    word_diff = abs(a_words - b_words)
    byte_diff = abs(a_bytes - b_bytes)
    print(f"Diff: {word_diff} words / {byte_diff} bytes")
    print()

    # Heuristic: cookies authenticate if either word count or byte size
    # differs by >5% of the anonymous baseline (with min 20 words / 1KB).
    word_threshold = max(20, b_words * 0.05)
    byte_threshold = max(1000, b_bytes * 0.05)
    significant = word_diff > word_threshold or byte_diff > byte_threshold

    if significant:
        print("VERDICT: AUTHENTICATED — cookies measurably change the response.")
        return 0
    print("VERDICT: NOT AUTHENTICATED — cookies have no effect on the response.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
