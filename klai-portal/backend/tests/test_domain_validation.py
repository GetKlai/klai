"""
Tests for domain validation utilities (SPEC-AUTH-006 R3).

Covers:
- Free email provider blocklist
- Domain format validation
- Domain normalization (lowercase, strip)
"""


class TestFreeEmailBlocklist:
    """Free email providers must be rejected when adding allowed domains."""

    def test_gmail_is_blocked(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("gmail.com") is True

    def test_hotmail_is_blocked(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("hotmail.com") is True

    def test_outlook_is_blocked(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("outlook.com") is True

    def test_yahoo_is_blocked(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("yahoo.com") is True

    def test_proton_is_blocked(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("proton.me") is True

    def test_corporate_domain_is_allowed(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("acme.nl") is False

    def test_case_insensitive(self) -> None:
        from app.services.domain_validation import is_free_email_provider

        assert is_free_email_provider("Gmail.COM") is True


class TestDomainNormalization:
    """Domain must be stored lowercase, stripped."""

    def test_lowercase(self) -> None:
        from app.services.domain_validation import normalize_domain

        assert normalize_domain("ACME.NL") == "acme.nl"

    def test_strip_whitespace(self) -> None:
        from app.services.domain_validation import normalize_domain

        assert normalize_domain("  acme.nl  ") == "acme.nl"

    def test_combined(self) -> None:
        from app.services.domain_validation import normalize_domain

        assert normalize_domain("  ACME.NL  ") == "acme.nl"


class TestDomainFormatValidation:
    """Domain format must be validated before storage."""

    def test_valid_domain(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("acme.nl") is True

    def test_valid_subdomain(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("mail.acme.nl") is True

    def test_invalid_no_tld(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("localhost") is False

    def test_invalid_with_protocol(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("https://acme.nl") is False

    def test_invalid_with_path(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("acme.nl/page") is False

    def test_invalid_empty(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("") is False

    def test_invalid_with_at(self) -> None:
        from app.services.domain_validation import is_valid_domain

        assert is_valid_domain("user@acme.nl") is False
