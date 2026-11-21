"""Which e-mail code factors count, while their enrolment is still unconfirmed.

Zitadel v4 activates the e-mail code factor the moment it is registered and
refuses to mail a session code for a user without it (``COMMAND-JKLJ3``,
v4.20.1), so the enrolment has to register before the user has proven they
receive the code. The enrolment therefore marks the user as unconfirmed before
it registers and clears the mark only when the mailed code checks out. A
marked factor never counts: ``/api/me`` keeps the user in set-up, and the next
login removes it, which is also how an abandoned or expired enrolment is
cleaned up without a background job.

A registered factor without a mark (one added before this mark existed) counts
as it always did.
"""

from typing import cast

import redis.asyncio as aioredis

from app.services.redis_client import get_redis_pool
from app.services.zitadel import zitadel

EMAIL_OTP_UNCONFIRMED_PREFIX = "email_otp_unconfirmed:"
# The pending enrolment session for one user; see ``_send_email_otp_enrolment_code``.
EMAIL_OTP_ENROLMENT_PREFIX = "email_otp_enrolment:"
# Five minutes is how long Zitadel keeps an e-mail code valid (OTPEmail
# generator default, v4.20.1).
EMAIL_OTP_ENROLMENT_TTL_S = 300


async def redis_pool() -> aioredis.Redis:
    pool = await get_redis_pool()
    if pool is None:
        raise RuntimeError("Redis is not configured")
    return cast("aioredis.Redis", pool)


async def remove_unconfirmed_email_otp(user_id: str) -> None:
    """Remove the e-mail code factor and both enrolment keys."""
    await zitadel.remove_email_otp(user_id)
    pool = await redis_pool()
    await pool.delete(f"{EMAIL_OTP_UNCONFIRMED_PREFIX}{user_id}", f"{EMAIL_OTP_ENROLMENT_PREFIX}{user_id}")


async def counted_second_factors(user_id: str, checks: list[str], *, remove_unconfirmed: bool) -> list[str]:
    """Drop ``otpEmail`` from ``checks`` while its enrolment is unconfirmed.

    ``remove_unconfirmed`` also removes that factor, for the login: an
    enrolment is never finished from there, so a mark seen at login belongs to
    one the user abandoned or let expire.
    """
    if "otpEmail" not in checks:
        return checks
    pool = await redis_pool()
    if not await pool.exists(f"{EMAIL_OTP_UNCONFIRMED_PREFIX}{user_id}"):
        return checks
    if remove_unconfirmed:
        await remove_unconfirmed_email_otp(user_id)
    return [check for check in checks if check != "otpEmail"]
