"""Domain validation utilities for the allowed-domains feature (SPEC-AUTH-006 R3)."""

import re

# Free email providers that cannot be used as org-wide allowed domains.
# An attacker could register gmail.com and auto-provision into any org.
FREE_EMAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        "126.com",
        "163.com",
        "aol.com",
        "bk.ru",
        "casema.nl",
        "chello.nl",
        "duck.com",
        "email.com",
        "fastmail.com",
        "free.fr",
        "gmail.com",
        "gmx.com",
        "gmx.de",
        "googlemail.com",
        "hetnet.nl",
        "home.nl",
        "hotmail.co.uk",
        "hotmail.com",
        "hotmail.nl",
        "hushmail.com",
        "icloud.com",
        "inbox.com",
        "kpnmail.nl",
        "laposte.net",
        "libero.it",
        "list.ru",
        "live.com",
        "live.nl",
        "mac.com",
        "mail.com",
        "mail.ru",
        "mailbox.org",
        "mailfence.com",
        "me.com",
        "msn.com",
        "orange.fr",
        "outlook.com",
        "outlook.nl",
        "planet.nl",
        "pm.me",
        "posteo.de",
        "proton.me",
        "protonmail.ch",
        "protonmail.com",
        "qq.com",
        "quicknet.nl",
        "rambler.ru",
        "rediffmail.com",
        "seznam.cz",
        "sina.com",
        "t-online.de",
        "telfort.nl",
        "tuta.com",
        "tutanota.com",
        "upcmail.nl",
        "virgilio.it",
        "wanadoo.fr",
        "wanadoo.nl",
        "web.de",
        "xs4all.nl",
        "yandex.com",
        "yandex.ru",
        "yahoo.com",
        "yahoo.co.uk",
        "yahoo.nl",
        "zeelandnet.nl",
        "ziggo.nl",
        "zoho.com",
        "zohomail.com",
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


def primary_domain_for_email_domain(domain: str) -> str:
    """Return the org-claimable primary domain, or empty for personal mail domains."""
    normalized = normalize_domain(domain)
    if is_free_email_provider(normalized):
        return ""
    return normalized


def is_valid_domain(domain: str) -> bool:
    """Return True if the domain has a valid format (no protocol, no path, has TLD)."""
    if not domain:
        return False
    normalized = normalize_domain(domain)
    return bool(_DOMAIN_RE.match(normalized))
