"""Domain validation utilities for the allowed-domains feature (SPEC-AUTH-006 R3)."""

import re

# Free email providers that cannot be used as org-wide allowed domains.
# An attacker could register gmail.com and auto-provision into any org.
FREE_EMAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "outlook.com",
        "yahoo.com",
        "live.com",
        "icloud.com",
        "proton.me",
        "gmx.com",
    }
)

# RFC-compliant domain regex: labels separated by dots, 2+ char TLD
_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.[a-z0-9-]{1,63})*\.[a-z]{2,}$")


def normalize_domain(domain: str) -> str:
    """Normalize a domain: lowercase + strip whitespace (C2.1)."""
    return domain.strip().lower()


def is_free_email_provider(domain: str) -> bool:
    """Return True if the domain is a free email provider (C3.3)."""
    return normalize_domain(domain) in FREE_EMAIL_PROVIDERS


def is_valid_domain(domain: str) -> bool:
    """Return True if the domain has a valid format (no protocol, no path, has TLD)."""
    if not domain:
        return False
    normalized = normalize_domain(domain)
    return bool(_DOMAIN_RE.match(normalized))
