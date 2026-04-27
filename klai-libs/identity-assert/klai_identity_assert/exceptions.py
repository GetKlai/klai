"""Exceptions raised by the identity-assert helper.

The library follows a fail-closed contract (REQ-7.2): any failure that prevents
arriving at a verified outcome MUST surface as a denial — either via
``VerifyResult.verified=False`` or via one of these exceptions when the caller
opts into raising mode.

Default mode returns a ``VerifyResult`` for every code path; exception mode is
opt-in via :meth:`IdentityAsserter.verify_or_raise` for callers that prefer
exception-driven flow control.
"""

from __future__ import annotations


class IdentityAssertError(Exception):
    """Base class for all identity-assert library errors."""


class PortalUnreachable(IdentityAssertError):
    """Network failure or 5xx response from portal /internal/identity/verify.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-7.2 mandates fail-closed behaviour: if
    portal cannot be reached, every consumer SHALL refuse the upstream
    operation. This exception is the explicit raise-mode signal; default
    return-mode surfaces the same condition as
    ``VerifyResult.deny("portal_unreachable")``.
    """


class IdentityDenied(IdentityAssertError):
    """Portal returned a verified=false response; raised only in opt-in mode.

    Default return-mode surfaces the same condition as a non-verified
    ``VerifyResult`` with the corresponding ``reason``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"identity_denied: {reason}")
        self.reason = reason
