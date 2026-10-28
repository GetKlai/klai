"""Layer 1 of the KB governance (SPEC-SLACK-001 §5): the KBs the workspace's key may search.

The list comes from the Partner API and is cached per team for 60 seconds, the
bound the spec puts on how long a revocation in the portal may take to reach
Slack. A cache entry is tied to the link it was fetched with, so a relink with
a new key never serves the previous key's list.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from app import db, repo
from app.clients.partner import KnowledgeBase, PartnerClient
from app.config import get_settings

CACHE_TTL_S = 60.0


class NotLinked(Exception):
    """The team has no active link to a Klai org (never connected, or revoked)."""


@dataclass(frozen=True)
class Workspace:
    team_id: str
    org_name: str
    partner: PartnerClient
    link_version: tuple[str, datetime]


@dataclass(frozen=True)
class _Entry:
    link_version: tuple[str, datetime]
    expires_at: float
    kbs: list[KnowledgeBase]


_cache: dict[str, _Entry] = {}


def _partner(base_url: str, api_key: str) -> PartnerClient:
    """Indirection so tests can inject an httpx MockTransport."""
    return PartnerClient(base_url, api_key)


async def get_workspace(team_id: str) -> Workspace:
    async with db.session() as s:
        link = await repo.get_link(s, team_id)
    if link is None or link.revoked_at is not None:
        raise NotLinked(team_id)
    return Workspace(
        team_id=team_id,
        org_name=link.org_name,
        partner=_partner(get_settings().portal_api_url, repo.link_api_key(link)),
        link_version=(link.key_id, link.linked_at),
    )


async def allowed_kbs(team_id: str, workspace: Workspace | None = None) -> list[KnowledgeBase]:
    """Raises NotLinked, or a PartnerError when the Partner API refuses or is down."""
    ws = workspace or await get_workspace(team_id)
    entry = _cache.get(team_id)
    now = time.monotonic()
    if entry is not None and entry.link_version == ws.link_version and entry.expires_at > now:
        return entry.kbs
    kbs = await ws.partner.list_knowledge_bases()
    _cache[team_id] = _Entry(link_version=ws.link_version, expires_at=now + CACHE_TTL_S, kbs=kbs)
    return kbs


def invalidate(team_id: str) -> None:
    _cache.pop(team_id, None)
