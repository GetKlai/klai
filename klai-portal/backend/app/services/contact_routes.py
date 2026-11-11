"""A widget's contact routes: where a visitor who needs a person is sent.

An organisation defines its own routes in ``widget_config.contact_routes`` (at
most six, exactly one default), so nothing about its departments lives in
code. Every reply at a human names one route (klai_chat_prompts.contact_reply
and its siblings): a contact request, "not found", "not further" and a subject
the widget does not answer. With more than one route a short call picks the
one that fits the visitor; it fails towards the default route.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urlsplit

import structlog
from klai_chat_prompts import RouteChannels
from pydantic import BaseModel, ConfigDict, EmailStr, Field, JsonValue, create_model, field_validator, model_validator

from app.core.config import Settings
from app.services.turn_judge import structured_judge_call

logger = structlog.get_logger()

MAX_ROUTES = 6
_PHONE_SHAPE = re.compile(r"[0-9 +()\-]+")
# A route's URLs land in a reply as a Markdown link target and in an href or an
# iframe src, so a value that could close the link or start another is refused.
_URL_UNSAFE = re.compile(r"[\s()<>\[\]\"'`\\]")
# The name, the note and the link label are rendered into the visitor's reply
# as Markdown: no link, no autolinked address or URL, no emphasis, one line.
_TEXT_UNSAFE = re.compile(r"[\[\]<>*_`\\@\x00-\x1f]|://|www\.", re.IGNORECASE)
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# The same budget as the referral call (off_topic_referral.py): it runs on a
# turn that already ends at a human, so it only delays a fixed reply.
_PICK_TIMEOUT_SECONDS = 2.5

PICK_SYSTEM_PROMPT = (
    "A visitor of a company's help chat needs a person. Choose the contact route whose description fits what the "
    "visitor needs now. When none fits better than another, choose the first route. Answer with the route id."
)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def phone_shape_problem(value: str | None) -> bool:
    """True when ``value`` is set and is not a phone number: digits, spaces and + - ( ), at least 6 digits."""
    return value is not None and not (_PHONE_SHAPE.fullmatch(value) and sum(c.isdigit() for c in value) >= 6)


class ContactRoute(BaseModel):
    """One contact route, validated at the admin boundary and read back as stored."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(default_factory=_new_id)
    # Visitor-facing.
    name: str = Field(min_length=1, max_length=60)
    # Model-facing: which questions belong here (the pick reads it).
    description: str = Field(default="", max_length=300)
    # Model-facing: subjects within this route the chat refers instead of answering.
    do_not_answer: str = Field(default="", max_length=500)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    booking_url: str | None = Field(default=None, max_length=2000)
    link_url: str | None = Field(default=None, max_length=2000)
    link_label: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=200)
    is_default: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _id_or_new(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return _new_id()
        return value

    @field_validator("id")
    @classmethod
    def _plain_id(cls, value: str) -> str:
        # The id goes into the pick's JSON schema and the record.
        if not _ID.fullmatch(value):
            raise ValueError("id must be 1 to 64 letters, digits, - or _")
        return value

    @field_validator("phone", "email", "booking_url", "link_url", "link_label", "note", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("phone")
    @classmethod
    def _phone_is_a_phone(cls, value: str | None) -> str | None:
        if phone_shape_problem(value):
            raise ValueError("phone must be a phone number: digits, spaces and + - ( ), at least 6 digits")
        return value

    @field_validator("booking_url", "link_url")
    @classmethod
    def _absolute_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or _URL_UNSAFE.search(value):
            raise ValueError("must be an absolute http(s) URL without spaces, quotes, brackets or parentheses")
        return value

    @field_validator("name", "note", "link_label")
    @classmethod
    def _plain_text(cls, value: str | None) -> str | None:
        if value is not None and _TEXT_UNSAFE.search(value):
            raise ValueError("must be plain text on one line: no link, address, URL or Markdown characters")
        return value

    @model_validator(mode="after")
    def _link_has_a_label(self) -> ContactRoute:
        if (self.link_url is None) != (self.link_label is None):
            raise ValueError("link_url and link_label go together")
        return self


def checked_routes(routes: list[ContactRoute]) -> list[ContactRoute]:
    """The route list as a whole: unique ids and exactly one default when it is not empty."""
    if len({route.id for route in routes}) != len(routes):
        raise ValueError("contact route ids must be unique")
    if routes and sum(route.is_default for route in routes) != 1:
        raise ValueError("exactly one contact route must be the default")
    return routes


def routes_from_config(config: dict[str, Any]) -> tuple[ContactRoute, ...]:
    return tuple(ContactRoute.model_validate(route) for route in config.get("contact_routes") or [])


def default_route(routes: tuple[ContactRoute, ...]) -> ContactRoute | None:
    return next((route for route in routes if route.is_default), None)


def subjects(routes: tuple[ContactRoute, ...]) -> str:
    """What the case is told this widget does not answer: every route's ``do_not_answer``."""
    return "; ".join(route.do_not_answer for route in routes if route.do_not_answer)


def channels(route: ContactRoute) -> RouteChannels:
    return RouteChannels(
        name=route.name,
        booking_url=route.booking_url,
        email=route.email,
        phone=route.phone,
        link_url=route.link_url,
        link_label=route.link_label,
        note=route.note,
    )


def other_channels(routes: tuple[ContactRoute, ...], route: ContactRoute) -> tuple[RouteChannels, ...]:
    """The widget's other routes, which a reply lists as the recovery from a wrong pick."""
    return tuple(channels(other) for other in routes if other.id != route.id)


def route_choice[M: BaseModel](base: type[M], ids: list[str]) -> type[M]:
    """``base`` with a ``route`` field whose JSON schema is the closed list ``ids``.

    A plain ``str`` that the caller checks against ``ids``, so an id outside
    the list is a failed pick and not a validation error of the whole reply.
    """
    enum: list[JsonValue] = list(ids)
    return create_model(
        f"{base.__name__}WithRoute", __base__=base, route=(str, Field(json_schema_extra={"enum": enum}))
    )


class _Pick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


async def pick_route(
    routes: tuple[ContactRoute, ...],
    latest: str,
    *,
    problem: str,
    product: str,
    settings: Settings,
    delegated_org_id: str | None = None,
) -> tuple[ContactRoute, str]:
    """The route a turn at a human names, and how it was picked: ``single``, ``model`` or ``failed``.

    One route needs no call. Any failure, timeout or id outside the list falls
    back to the default route.
    """
    if len(routes) == 1:
        return routes[0], "single"
    listed = "\n".join(f"- {route.id}: {route.name}: {route.description}" for route in routes)
    result = await structured_judge_call(
        name="contact_route_pick",
        system_prompt=PICK_SYSTEM_PROMPT,
        user_content=(
            f"Visitor: {latest[:800]}\nProblem: {problem or '(unknown)'}\nProduct: {product or '(unknown)'}"
            f"\n\nRoutes:\n{listed}"
        ),
        schema=route_choice(_Pick, [route.id for route in routes]),
        timeout_seconds=_PICK_TIMEOUT_SECONDS,
        delegated_org_id=delegated_org_id,
        settings=settings,
    )
    chosen = getattr(result, "route", None)
    picked = next((route for route in routes if route.id == chosen), None)
    if picked is not None:
        return picked, "model"
    fallback = default_route(routes)
    assert fallback is not None, "a route list with more than one route has a default (checked_routes)"
    return fallback, "failed"
