"""What every `/klai` subcommand handler receives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slack_bolt.async_app import AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient


@dataclass(frozen=True)
class CommandContext:
    args: str
    command: dict[str, Any]
    client: AsyncWebClient
    respond: AsyncRespond
