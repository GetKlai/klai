"""`/klai <subcommand>` dispatcher.

Slack wants an ack within 3 seconds, so the ack only shows a short ephemeral
text and the real work runs in Bolt's lazy listener. A subcommand registers
its ack text (None = empty ack, e.g. when it opens a modal) and its handler in
``SUBCOMMANDS``; slice A2 adds ``kb`` and ``help`` there.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from slack_bolt.async_app import AsyncAck, AsyncApp, AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient

from app.clients.portal import PortalClient
from app.commands import connect
from app.commands.context import CommandContext

NOT_AVAILABLE = "Nog niet beschikbaar."


@dataclass(frozen=True)
class Subcommand:
    ack_text: str | None
    run: Callable[[CommandContext], Awaitable[None]]


SUBCOMMANDS: dict[str, Subcommand] = {
    "connect": Subcommand(ack_text=connect.ACK_TEXT, run=connect.run),
}


def parse(text: str | None) -> tuple[str, str]:
    name, _, rest = (text or "").strip().partition(" ")
    return name.lower(), rest.strip()


async def ack_klai(ack: AsyncAck, command: dict[str, Any]) -> None:
    subcommand = SUBCOMMANDS.get(parse(command.get("text"))[0])
    if subcommand is None:
        await ack(text=NOT_AVAILABLE)
    elif subcommand.ack_text is None:
        await ack()
    else:
        await ack(text=subcommand.ack_text)


def register(app: AsyncApp, portal: PortalClient) -> None:
    async def run_klai(
        command: dict[str, Any], client: AsyncWebClient, respond: AsyncRespond
    ) -> None:
        name, args = parse(command.get("text"))
        subcommand = SUBCOMMANDS.get(name)
        if subcommand is None:
            return
        await subcommand.run(
            CommandContext(
                args=args, command=command, client=client, respond=respond, portal=portal
            )
        )

    app.command("/klai")(ack=ack_klai, lazy=[run_klai])
