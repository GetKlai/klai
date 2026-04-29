# SPEC-SEC-INTERNAL-001 REQ-6 fixture: every line below is an intentional
# violation of `rules/no-string-compare-on-secret.yml`. Running ast-grep
# against this file MUST exit non-zero with the rule's error message.
#
# Do NOT import this file. It is a static regression fixture for the rule
# and is not on any service's import path. The rule's ``files:`` glob
# does not match this directory under normal CI runs; the rule is
# exercised against this fixture only via an explicit ``sg scan`` of
# the fixture path (see test-runner step in the rule's per-service
# workflow integration).

# ruff: noqa


def _bad_taxonomy_internal(token: str, secret: str) -> bool:
    # AC-1.2 violation: secret-shaped LHS, `!=` on a Bearer string.
    return token != f"Bearer {secret}"


def _bad_mailer_internal(provided: str, internal_secret: str) -> bool:
    # Finding 6 violation: bare `!=` on the configured secret.
    return provided != internal_secret


def _bad_taxonomy_eq(token: str, internal_secret: str) -> bool:
    # Variant: `==` instead of `!=`.
    return token == internal_secret


def _bad_api_key_check(provided_api_key: str, expected_api_key: str) -> bool:
    # Demonstrates the rule catches an api_key-named variable too.
    return provided_api_key == expected_api_key


def _bad_webhook_secret(received: str, webhook_secret: str) -> bool:
    return received != webhook_secret
