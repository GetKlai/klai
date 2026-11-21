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
from collections.abc import Callable
from typing import Any, NamedTuple
from urllib.parse import urlsplit

import structlog
from klai_chat_prompts import RouteChannels
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    JsonValue,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

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
_TEXT_UNSAFE = re.compile(r"[\[\]<>*_`\\@#~|\x00-\x1f]|://|www\.|^(?:[-+>]|\d+[.)])(?:\s|$)", re.IGNORECASE)
# Markdown in an e-mail address's local part: emphasis, strikethrough, code.
_EMAIL_UNSAFE = re.compile(r"[*~`]|(?:^|[^A-Za-z0-9])_|_(?:[^A-Za-z0-9]|$)")
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
    # The tenant's own referral for this route's do_not_answer subjects, sent
    # verbatim when the referral call cannot name the subject. Free text like
    # the off_topic_reply it replaces (same limit, rendered the same way); used
    # for the referral only.
    referral_text: str | None = Field(default=None, max_length=500)
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

    @field_validator("phone", "email", "booking_url", "link_url", "link_label", "note", "referral_text", mode="before")
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

    @field_validator("email")
    @classmethod
    def _no_markdown_in_the_address(cls, value: str | None) -> str | None:
        # The address is rendered into the reply as it is. first_last@ stays
        # plain (an underscore inside a word is not emphasis), but *, ~, a
        # backtick or an underscore at the edge of a word would format the text.
        if value is not None and _EMAIL_UNSAFE.search(value.partition("@")[0]):
            raise ValueError("email may not contain *, ~, a backtick, or an underscore at the start or end of a word")
        return value

    @model_validator(mode="after")
    def _link_has_a_label(self) -> ContactRoute:
        if (self.link_url is None) != (self.link_label is None):
            raise ValueError("link_url and link_label go together")
        return self


class SavedContactRoute(ContactRoute):
    """A route as the admin saves it: reachable as well as valid.

    Only the save requests use it. Reading a stored row (and the response
    built from it) uses ``ContactRoute``, so a route saved before this rule, or
    a referral-only route the migration made, never takes a widget down.
    """

    @model_validator(mode="after")
    def _reachable(self) -> SavedContactRoute:
        # A route only reached through its referral text is the old off-topic
        # setting of a widget without channels; it keeps referring.
        if not (self.phone or self.email or self.booking_url or self.link_url or self.referral_text):
            raise ValueError(
                "a contact route needs a way to reach it: phone, email, booking_url or link_url "
                "(or a referral_text, for a route that only refers subjects)"
            )
        return self


def checked_routes(routes: list[ContactRoute]) -> list[ContactRoute]:
    """The route list as a whole: unique ids and exactly one default when it is not empty."""
    if len({route.id for route in routes}) != len(routes):
        raise ValueError("contact route ids must be unique")
    if routes and sum(route.is_default for route in routes) != 1:
        raise ValueError("exactly one contact route must be the default")
    return routes


def routes_from_config(
    config: dict[str, Any], *, org_id: int | None = None, widget_id: str | None = None
) -> tuple[ContactRoute, ...]:
    """The stored routes, read tolerantly: one bad value never takes the widget or the admin list down.

    The admin API writes routes strictly, but a row can also hold what the
    migration copied from settings the old code never validated, or what SQL
    wrote. So, like ``conversation_starters``, reading repairs instead of
    raising: an invalid optional value is dropped, a route without a usable
    name or that is not an object is skipped, a repeated id gets a new one,
    routes beyond the limit are left out and the "exactly one default" rule is
    restored. Each repair logs ``contact_route_repaired`` with the field name,
    never the value.
    """

    def repaired(field: str) -> None:
        logger.warning("contact_route_repaired", org_id=org_id, widget_id=widget_id, field=field)

    stored = config.get("contact_routes") or []
    if not isinstance(stored, list):
        repaired("contact_routes")
        stored = []
    routes: list[ContactRoute] = []
    for raw in stored:
        route = _salvaged(raw, repaired)
        if route is None:
            continue
        if any(other.id == route.id for other in routes):
            repaired("id")
            route = route.model_copy(update={"id": _new_id()})
        routes.append(route)
    if len(routes) > MAX_ROUTES:
        repaired("contact_routes")
        routes = routes[:MAX_ROUTES]
    defaults = [route for route in routes if route.is_default]
    if routes and len(defaults) != 1:
        repaired("is_default")
        keep = defaults[0] if defaults else routes[0]
        routes = [route.model_copy(update={"is_default": route is keep}) for route in routes]
    return tuple(routes)


_LINK_FIELDS = ("link_url", "link_label")


def _salvaged(raw: object, repaired: Callable[[str], None]) -> ContactRoute | None:
    """``raw`` as a route, without the values that fail validation; ``None`` when no route is left."""
    if not isinstance(raw, dict):
        repaired("route")
        return None
    values: dict[str, Any] = dict(raw)
    for _ in ContactRoute.model_fields:
        try:
            return ContactRoute.model_validate(values)
        except ValidationError as error:
            # A model-level error has no field: it is the link without its label.
            fields = {str(problem["loc"][0]) for problem in error.errors() if problem["loc"]} or set(_LINK_FIELDS)
        if "name" in fields:
            repaired("name")
            return None
        for field in sorted(fields):
            repaired(field)
            for dropped in _LINK_FIELDS if field in _LINK_FIELDS else (field,):
                values.pop(dropped, None)
    return None


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
        referral_text=route.referral_text,
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
    result = await structured_judge_call(
        name="contact_route_pick",
        system_prompt=PICK_SYSTEM_PROMPT,
        user_content=f"Visitor: {latest[:800]}\nProblem: {problem or '(unknown)'}\nProduct: {product or '(unknown)'}"
        f"\n\nRoutes:\n{_listed(routes)}",
        schema=route_choice(_Pick, [route.id for route in routes]),
        timeout_seconds=_PICK_TIMEOUT_SECONDS,
        delegated_org_id=delegated_org_id,
        settings=settings,
    )
    return _chosen(routes, getattr(result, "route", None))


def _listed(routes: tuple[ContactRoute, ...]) -> str:
    return "\n".join(f"- {route.id}: {route.name}: {route.description}" for route in routes)


def _chosen(routes: tuple[ContactRoute, ...], chosen: object) -> tuple[ContactRoute, str]:
    """The route with id ``chosen`` ("model"), else the default ("failed")."""
    picked = next((route for route in routes if route.id == chosen), None)
    if picked is not None:
        return picked, "model"
    fallback = default_route(routes)
    assert fallback is not None, "a route list with more than one route has a default (checked_routes)"
    return fallback, "failed"


EXIT_KINDS = ("question", "request", "closing")

EXIT_SYSTEM_PROMPT = (
    "A visitor of a company's help chat sent a message that the help articles do not answer. Decide what kind of "
    "message it is.\n"
    "- question: the visitor asks for information or help that the articles could have covered. Example: how to "
    "reset a password; whether a plan includes a feature; why a screen stays empty.\n"
    "- request: the visitor asks the company to do, change, process, order or arrange something for them, which "
    "needs a person. Example: please change the name on the contract; we need three more units, how do we get them; "
    "can you cancel my order.\n"
    "- closing: nothing is asked. An acknowledgement, thanks, a greeting, or a remark that they will try again later "
    "or that it worked. Example: great, thanks; I will try again tomorrow; good morning.\n"
    "When unsure between question and another kind, answer question: a closing wrongly given to a real question "
    "ends the help early."
)
_ROUTE_PROMPT = (
    " Also choose the contact route whose description fits what the visitor needs now; when none fits better than "
    "another, choose the first route."
)


class Exit(NamedTuple):
    """How a turn that ends in a fixed "not found" is read: its ``kind``, by whom (``model``, ``failed`` or ``rule``), and its route."""

    kind: str
    how: str
    route: tuple[ContactRoute, str] | None


class _ExitKind(_Pick):
    kind: str = Field(json_schema_extra={"enum": list(EXIT_KINDS)})


async def pick_exit(
    routes: tuple[ContactRoute, ...],
    latest: str,
    previous_reply: str,
    *,
    first_turn: bool,
    frustrated: bool = False,
    problem: str,
    product: str,
    settings: Settings,
    delegated_org_id: str | None = None,
) -> Exit:
    """What the visitor's message is (``question``, ``request`` or ``closing``) and, with several routes, the route.

    One call for both. A failure, a timeout or an unknown value is a
    ``question``, which is the reply the exit always gave. ``closing`` is
    refused in code on a first message, on a frustrated turn (the not-found reply
    carries the appointment) and on a message with a question mark, because a real question called a closing ends the help early.
    """
    prompt, content, schema = (
        EXIT_SYSTEM_PROMPT,
        f"Visitor: {latest[:800]}\nPrevious reply: {previous_reply[:400]}",
        _ExitKind,
    )
    if len(routes) > 1:
        prompt += _ROUTE_PROMPT
        content += (
            f"\nProblem: {problem or '(unknown)'}\nProduct: {product or '(unknown)'}\n\nRoutes:\n{_listed(routes)}"
        )
        schema = route_choice(_ExitKind, [route.id for route in routes])
    result = await structured_judge_call(
        name="exit_kind",
        system_prompt=prompt,
        user_content=content,
        schema=schema,
        timeout_seconds=_PICK_TIMEOUT_SECONDS,
        delegated_org_id=delegated_org_id,
        settings=settings,
    )
    kind, how = getattr(result, "kind", None), "model"
    if kind not in EXIT_KINDS:
        kind, how = "question", "failed"
    elif kind == "closing" and (first_turn or frustrated or "?" in latest or "\uff1f" in latest):
        kind, how = "question", "rule"
    if len(routes) > 1:
        route = _chosen(routes, getattr(result, "route", None))
        if route[1] == "failed":
            # An id outside the list is an invalid answer as a whole, not only a failed route pick.
            kind, how = "question", "failed"
    else:
        route = (routes[0], "single") if routes else None
    return Exit(kind, how, route)
