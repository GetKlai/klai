"""`/klai <subcommand>` dispatcher.

Slack wants an ack within 3 seconds, so the ack is empty and the real work
runs in Bolt's lazy listener. A subcommand registers its handler in
``SUBCOMMANDS``. An unknown or missing subcommand gets the help text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from slack_bolt.async_app import AsyncAck, AsyncApp, AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient

from app.commands import help as help_cmd
from app.commands import kb
from app.commands.context import CommandContext


@dataclass(frozen=True)
class Subcommand:
    run: Callable[[CommandContext], Awaitable[None]]


SUBCOMMANDS: dict[str, Subcommand] = {
    "kb": Subcommand(run=kb.run),
    "help": Subcommand(run=help_cmd.run),
}


def parse(text: str | None) -> tuple[str, str]:
    name, _, rest = (text or "").strip().partition(" ")
    return name.lower(), rest.strip()


def lookup(text: str | None) -> tuple[Subcommand, str]:
    name, args = parse(text)
    return SUBCOMMANDS.get(name, SUBCOMMANDS["help"]), args


async def ack_klai(ack: AsyncAck) -> None:
    await ack()


def register(app: AsyncApp) -> None:
    async def run_klai(
        command: dict[str, Any], client: AsyncWebClient, respond: AsyncRespond
    ) -> None:
        subcommand, args = lookup(command.get("text"))
        await subcommand.run(
            CommandContext(args=args, command=command, client=client, respond=respond)
        )

    app.command("/klai")(ack=ack_klai, lazy=[run_klai])
