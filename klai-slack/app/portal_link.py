"""Portal-driven Slack link (SPEC-SLACK-001 §8 target state): the admin never sees a key.

The portal sends the admin's browser to ``/slack/connect?token=<jwt>``. The
token is HS256-signed by portal-api with the shared internal secret and carries
``typ == "slack_link"``, ``exp`` and ``return_url`` (the portal page to come back
to). klai-slack parks it in ``pending_links`` under a fresh Bolt OAuth state and
sends the browser to Slack's consent screen. Bolt checks that state (store and
browser cookie) on the callback, so only the browser that started the flow can
finish it; the success callback then trades the token for a minted key at
``POST /internal/slack/link-oauth`` and stores the link.

The browser lands back on ``return_url`` with ``?slack=`` set to ``connected``,
``already_linked`` (portal 409), ``denied`` (cancelled in Slack, or the org
lacks the ``slack`` unlock) or ``failed`` (anything else; start again from the
portal). An OAuth callback without a pending link is a plain ``/slack/install``
and keeps Bolt's own pages, so ``/klai connect`` stays usable.
"""

from __future__ import annotations

from typing import Any

import jwt
import structlog
from fastapi.responses import HTMLResponse, RedirectResponse
from slack_bolt import BoltResponse
from slack_bolt.oauth.async_callback_options import (
    AsyncCallbackOptions,
    AsyncFailureArgs,
    AsyncSuccessArgs,
)
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.oauth.installation_store.models.installation import Installation
from starlette.responses import Response

from app import db, repo
from app.clients.portal import (
    LinkConflict,
    LinkFeatureLocked,
    LinkKeyInvalid,
    PortalClient,
    PortalUnavailable,
)
from app.services.linking import store_link

logger = structlog.get_logger()

LINK_TOKEN_TYPE = "slack_link"
INVALID_LINK = "Deze koppellink is ongeldig of verlopen; start opnieuw vanuit de Klai-portal."
INVALID_LINK_PAGE = (
    '<!DOCTYPE html><html lang="nl"><head><meta charset="utf-8"><title>Klai</title></head>'
    f"<body><p>{INVALID_LINK}</p></body></html>"
)


def decode_link_token(token: str, secret: str, *, verify_exp: bool = True) -> dict[str, Any] | None:
    """The claims of a portal link token, or None when signature, ``exp`` or ``typ`` fail."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp"], "verify_exp": verify_exp},
        )
    except jwt.InvalidTokenError:
        return None
    return claims if claims.get("typ") == LINK_TOKEN_TYPE else None


async def start(token: str | None, oauth: AsyncOAuthSettings, secret: str) -> Response:
    """``GET /slack/connect``: park the token under a new OAuth state and go to Slack."""
    if not token or decode_link_token(token, secret) is None:
        logger.info("slack_portal_link_token_rejected")
        return HTMLResponse(INVALID_LINK_PAGE, status_code=400)
    state = await oauth.state_store.async_issue()
    async with db.session() as s:
        await repo.add_pending_link(s, state, token)
    # The same URL and state cookie as Bolt's /slack/install, so its callback accepts it.
    response = RedirectResponse(oauth.authorize_url_generator.generate(state), status_code=302)
    response.headers["Set-Cookie"] = oauth.state_utils.build_set_cookie_for_new_state(state)
    return response


def _redirect(settings: AsyncOAuthSettings, return_url: str, outcome: str) -> BoltResponse:
    separator = "&" if "?" in return_url else "?"
    return BoltResponse(
        status=302,
        body="",
        headers={
            "Location": f"{return_url}{separator}slack={outcome}",
            "Set-Cookie": [settings.state_utils.build_set_cookie_for_deletion()],
        },
    )


async def _pop_token(request: AsyncBoltRequest) -> str | None:
    state = request.query.get("state", [None])[0]
    if not state:
        return None
    async with db.session() as s:
        return await repo.pop_pending_link(s, state)


async def _link(
    portal: PortalClient, token: str, installation: Installation
) -> tuple[str, str | None]:
    """Trade the link token for a key and store it: the ``?slack=`` outcome, plus the
    portal's ``return_url`` once the portal accepted the token."""
    team_id = installation.team_id
    if team_id is None:
        # Enterprise-wide installs carry no team; the manifest does not offer them.
        outcome, reason = "failed", "no_team_id"
    else:
        try:
            result = await portal.link_oauth(
                token, team_id, installation.team_name or "", installation.user_id
            )
        except LinkConflict as exc:
            outcome, reason = "already_linked", exc.code
        except LinkFeatureLocked:
            outcome, reason = "denied", "feature_locked"
        except (LinkKeyInvalid, PortalUnavailable) as exc:
            outcome, reason = "failed", type(exc).__name__
        else:
            stored = await store_link(
                portal,
                team_id=team_id,
                result=result,
                api_key=result.api_key,
                linked_by_slack_user_id=installation.user_id,
            )
            return ("connected" if stored else "failed"), result.return_url
    logger.warning("slack_portal_link_failed", team_id=team_id, outcome=outcome, reason=reason)
    return outcome, None


def callback_options(portal: PortalClient, secret: str) -> AsyncCallbackOptions:
    async def back_to_portal(args: AsyncFailureArgs, token: str, outcome: str) -> BoltResponse:
        # verify_exp=False: the token may expire while the admin sits on Slack's consent
        # screen, and its signed return_url is still the right place to report that.
        claims = decode_link_token(token, secret, verify_exp=False)
        return_url = claims.get("return_url") if claims else None
        if not isinstance(return_url, str) or not return_url:
            return await args.default.failure(args)
        return _redirect(args.settings, return_url, outcome)

    async def success(args: AsyncSuccessArgs) -> BoltResponse:
        token = await _pop_token(args.request)
        if token is None:
            return await args.default.success(args)
        outcome, return_url = await _link(portal, token, args.installation)
        if return_url is not None:
            return _redirect(args.settings, return_url, outcome)
        failure_args = AsyncFailureArgs(
            request=args.request,
            reason=outcome,
            suggested_status_code=500,
            settings=args.settings,
            default=args.default,
        )
        return await back_to_portal(failure_args, token, outcome)

    async def failure(args: AsyncFailureArgs) -> BoltResponse:
        token = await _pop_token(args.request)
        if token is None:
            return await args.default.failure(args)
        outcome = "denied" if args.reason == "access_denied" else "failed"
        logger.info("slack_portal_link_aborted", outcome=outcome, reason=args.reason)
        return await back_to_portal(args, token, outcome)

    return AsyncCallbackOptions(success=success, failure=failure)
