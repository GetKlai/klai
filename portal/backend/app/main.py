import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import select

from app.api import me, signup
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.internal import router as internal_router
from app.api.meetings import ACTIVE_STATUSES, _run_transcription
from app.api.meetings import router as meetings_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.meetings import VexaMeeting
from app.services.vexa import parse_meeting_url, vexa
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

# Statuses that the vexa bot-manager reports while actively in a call
_VEXA_BOT_ACTIVE_STATUSES = {"joining", "in_call_recording", "recording", "waiting", "starting", "pending"}
_BOT_POLL_INTERVAL = 30  # seconds


async def _bot_poll_loop() -> None:
    """Background task: poll Vexa every 30 s for active meetings.

    When a bot is gone (404) or reports a non-active status the meeting was
    ended by the host. We trigger transcription so the portal catches up even
    when the Vexa webhook was never fired.
    """
    await asyncio.sleep(15)  # short initial delay so the app is fully started
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES))
                )
                active_meetings = list(result.scalars().all())

            for meeting in active_meetings:
                ref = parse_meeting_url(meeting.meeting_url)
                if ref is None:
                    continue

                bot_ended = False
                try:
                    status_resp = await vexa.get_bot_status(ref.platform, ref.native_meeting_id)
                    bot_status = status_resp.get("status", "")
                    bot_ended = bool(bot_status) and bot_status not in _VEXA_BOT_ACTIVE_STATUSES
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        bot_ended = True
                    else:
                        logger.warning("Bot poll status check failed for %s: %s", meeting.id, exc)
                except Exception as exc:
                    logger.warning("Bot poll error for %s: %s", meeting.id, exc)

                if not bot_ended:
                    continue

                logger.info("Bot poll: meeting %s appears ended, triggering transcription", meeting.id)
                async with AsyncSessionLocal() as db:
                    # Re-fetch with status guard to avoid race with webhook
                    m = await db.scalar(
                        select(VexaMeeting).where(
                            VexaMeeting.id == meeting.id,
                            VexaMeeting.status.in_(ACTIVE_STATUSES),
                        )
                    )
                    if m is None:
                        continue  # Already handled by webhook or another poll

                    m.status = "processing"
                    m.ended_at = m.ended_at or datetime.now(UTC)
                    await db.commit()
                    await db.refresh(m)

                    await _run_transcription(m, db)
                    await db.commit()

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bot poll loop: unexpected error")

        await asyncio.sleep(_BOT_POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Validate the Zitadel PAT before accepting traffic.
    # A wrong PAT makes ALL auth endpoints fail with 401, so crash early.
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{settings.zitadel_base_url}/auth/v1/users/me",
            headers={"Authorization": f"Bearer {settings.zitadel_pat}"},
        )
    if resp.status_code != 200:
        logger.critical(
            "ZITADEL_PAT validation failed (HTTP %s). "
            "The PAT in the environment is invalid or corrupted. "
            "Fix PORTAL_API_ZITADEL_PAT in .env and restart.",
            resp.status_code,
        )
        raise SystemExit(1)
    logger.info("Zitadel PAT validated successfully")

    poll_task = asyncio.create_task(_bot_poll_loop())
    logger.info("Bot poll loop started (interval: %ds)", _BOT_POLL_INTERVAL)

    yield

    poll_task.cancel()
    await asyncio.gather(poll_task, return_exceptions=True)
    await vexa.close()
    await zitadel.close()


app = FastAPI(
    title="Klai Portal API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_authenticated(request: Request, call_next: object) -> Response:
    """Prevent caching of all API responses (they are user-specific)."""
    response: Response = await call_next(request)  # type: ignore[arg-type]
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(signup.router)
app.include_router(me.router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(webhooks_router)
app.include_router(internal_router)
app.include_router(meetings_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
