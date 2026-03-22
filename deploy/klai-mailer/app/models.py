"""
Pydantic models for the Zitadel HTTP notification provider payload.

Zitadel pre-renders the configured message texts (subject, greeting, body, etc.)
and sends them in `templateData`. klai-mailer's job is to wrap this content in
Klai-branded HTML and send it via SMTP.

The message content lives in Zitadel (Instance > Message Texts),
not in klai-mailer. klai-mailer owns the HTML form; Zitadel owns the content.

Reference: https://zitadel.com/docs/guides/manage/customize/notification-providers
Verify actual field names against a live instance using POST /debug.
"""

from pydantic import BaseModel


class ContextInfo(BaseModel):
    eventType: str | None = None
    recipientEmailAddress: str | None = None
    provider: dict | None = None


class TemplateData(BaseModel):
    """Pre-rendered message text fields from Zitadel's configured message texts."""
    title: str | None = None
    preHeader: str | None = None
    subject: str | None = None
    greeting: str | None = None
    text: str | None = None
    url: str | None = None
    buttonText: str | None = None
    footerText: str | None = None


class ZitadelArgs(BaseModel):
    """Raw event arguments sent alongside templateData."""
    Code: str | None = None
    Expiry: str | None = None
    ApplicationName: str | None = None


class ZitadelPayload(BaseModel):
    contextInfo: ContextInfo | None = None
    templateData: TemplateData | None = None
    args: ZitadelArgs | None = None

    def event_type(self) -> str:
        return (self.contextInfo and self.contextInfo.eventType) or ""

    def recipient_email(self) -> str:
        return (self.contextInfo and self.contextInfo.recipientEmailAddress) or ""

    def button_url(self) -> str:
        return (self.templateData and self.templateData.url) or ""

    def has_button(self) -> bool:
        return bool(self.templateData and self.templateData.buttonText) and bool(self.button_url())

    def subject(self) -> str:
        return (self.templateData and self.templateData.subject) or "Message from Klai"

    def pre_header(self) -> str:
        return (self.templateData and self.templateData.preHeader) or ""

    def button_text(self) -> str:
        return (self.templateData and self.templateData.buttonText) or ""

    def footer_note(self) -> str:
        return (self.templateData and self.templateData.footerText) or ""

    def preferred_language(self) -> str | None:
        """
        Return the user's preferred language from the payload, or None if unknown.

        Zitadel's HTTP notification provider does NOT include preferredLanguage in its
        webhook payload (confirmed against source: internal/notification/types/user_email.go).
        This always returns None. The caller must handle None explicitly — no silent fallback.
        """
        return None
