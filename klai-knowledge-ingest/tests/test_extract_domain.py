"""``extract_domain`` normalisation (A3, bulk-path defects block A).

``knowledge.crawl_domains`` is keyed by ``extract_domain(url)`` for BOTH
the CSS-selector cache and the adaptive rate-limit override (see
``domain_selectors`` module docstring). A bare ``urlparse(url).netloc``
lets ``Example.com``, ``example.com``, ``example.com:443`` and
``example.com.`` collide onto four separate rows instead of one — this
locks in the normalisation that prevents that split.
"""

from __future__ import annotations

import pytest

from knowledge_ingest.domain_selectors import extract_domain


@pytest.mark.parametrize(
    ("url", "expected_domain"),
    [
        # Baseline — already-normalised URL is unchanged.
        ("https://example.com/page", "example.com"),
        # Uppercase hostname must lowercase.
        ("https://Example.com/page", "example.com"),
        ("https://EXAMPLE.COM/page", "example.com"),
        # Default port for the URL's own scheme is stripped.
        ("https://example.com:443/page", "example.com"),
        ("http://example.com:80/page", "example.com"),
        # A non-default port is a genuinely different origin and is kept.
        ("https://example.com:8080/page", "example.com:8080"),
        # https default port (443) explicitly kept when scheme is http —
        # it is NOT http's default, so it must NOT be stripped.
        ("http://example.com:443/page", "example.com:443"),
        # Trailing dot (absolute DNS name) is stripped.
        ("https://example.com./page", "example.com"),
        ("https://Example.com./page", "example.com"),
        # IDNA normalisation: a Unicode hostname collides with its punycode
        # form onto the same key.
        ("https://münchen.example/page", "xn--mnchen-3ya.example"),
        ("https://xn--mnchen-3ya.example/page", "xn--mnchen-3ya.example"),
        # Combination: uppercase + default port + trailing dot together.
        ("https://Example.com.:443/page", "example.com"),
    ],
)
def test_extract_domain_normalises_to_expected_key(url: str, expected_domain: str) -> None:
    assert extract_domain(url) == expected_domain


def test_extract_domain_variants_of_the_same_site_collide_onto_one_key() -> None:
    """The regression this fix exists for: these four URLs are the SAME
    site and MUST produce the SAME domain key, or they silently become
    four independent rate limits / selector rows."""
    variants = [
        "https://Example.com/page",
        "https://example.com/page",
        "https://example.com:443/page",
        "https://example.com./page",
    ]
    domains = {extract_domain(u) for u in variants}
    assert domains == {"example.com"}
