#!/usr/bin/env python3
"""Sync Klai's source-controlled listmonk templates to a listmonk instance."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEPLOY_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = DEPLOY_DIR / "listmonk" / "templates"


@dataclass(frozen=True)
class TemplateSpec:
    slug: str
    name: str
    template_type: str
    body_path: Path
    subject: str = ""
    id_env: str = ""


TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(
        slug="campaign",
        name="Klai branded campaign",
        template_type="campaign",
        body_path=TEMPLATE_DIR / "klai-branded-campaign.html",
        id_env="LISTMONK_TEMPLATE_KLAI_CAMPAIGN_ID",
    ),
    TemplateSpec(
        slug="onboarding_invite",
        name="Klai onboarding invite",
        template_type="tx",
        subject="Welcome to Klai, you're in",
        body_path=TEMPLATE_DIR / "onboarding-invite.tx.html",
        id_env="LISTMONK_TX_ONBOARDING_TEMPLATE_ID",
    ),
)

TEMPLATES_BY_SLUG = {spec.slug: spec for spec in TEMPLATES}


class ListmonkError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    slug: str
    action: str
    template_id: int | None
    name: str


class ListmonkClient:
    def __init__(self, *, base_url: str, api_user: str, api_token: str) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ListmonkError("LISTMONK_URL must be an http(s) URL")
        self.base_url = base_url.rstrip("/")
        auth = f"{api_user}:{api_token}".encode()
        self.auth_header = "Basic " + base64.b64encode(auth).decode("ascii")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Authorization": self.auth_header}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        req = urllib.request.Request(  # noqa: S310 - base_url scheme is validated in __init__
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - operator-provided listmonk URL
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise ListmonkError(f"listmonk {method} {path} returned {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ListmonkError(f"listmonk {method} {path} failed: {exc}") from exc

        if not body.strip():
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ListmonkError(f"listmonk {method} {path} returned non-object JSON")
        return parsed


def build_payload(spec: TemplateSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": spec.name,
        "type": spec.template_type,
        "body": spec.body_path.read_text(encoding="utf-8").strip(),
    }
    if spec.subject:
        payload["subject"] = spec.subject
    return payload


def _positive_env_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ListmonkError(f"{name} must be an integer, got {raw!r}") from exc
    return value if value > 0 else None


def _extract_templates(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return [item for item in data["results"] if isinstance(item, dict)]
    return []


def _extract_id(response: dict[str, Any]) -> int | None:
    data = response.get("data")
    if isinstance(data, dict):
        raw_id = data.get("id")
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        raw_id = data[0].get("id")
    else:
        raw_id = None
    if isinstance(raw_id, int):
        return raw_id
    if isinstance(raw_id, str) and raw_id.isdigit():
        return int(raw_id)
    return None


def _find_template_id(client: Any, spec: TemplateSpec) -> int | None:
    response = client.request("GET", "/api/templates")
    for template in _extract_templates(response):
        if template.get("name") == spec.name and template.get("type") == spec.template_type:
            raw_id = template.get("id")
            if isinstance(raw_id, int):
                return raw_id
            if isinstance(raw_id, str) and raw_id.isdigit():
                return int(raw_id)
    return None


def sync_template(
    client: Any,
    spec: TemplateSpec,
    *,
    dry_run: bool = False,
    set_campaign_default: bool = False,
) -> SyncResult:
    payload = build_payload(spec)
    configured_id = _positive_env_int(spec.id_env) if spec.id_env else None

    if dry_run:
        action = "update" if configured_id else "upsert"
        print(json.dumps({"slug": spec.slug, "action": action, "template_id": configured_id, "payload": payload}, indent=2))
        return SyncResult(slug=spec.slug, action=f"dry-run-{action}", template_id=configured_id, name=spec.name)

    template_id = configured_id or _find_template_id(client, spec)
    if template_id is not None:
        client.request("PUT", f"/api/templates/{template_id}", payload)
        action = "updated"
    else:
        response = client.request("POST", "/api/templates", payload)
        template_id = _extract_id(response)
        action = "created"

    if set_campaign_default and spec.template_type == "campaign":
        if template_id is None:
            raise ListmonkError("cannot set campaign default because listmonk did not return a template id")
        client.request("PUT", f"/api/templates/{template_id}/default")

    return SyncResult(slug=spec.slug, action=action, template_id=template_id, name=spec.name)


def _client_from_env() -> ListmonkClient:
    missing = [
        name
        for name in ("LISTMONK_URL", "LISTMONK_API_USER", "LISTMONK_API_TOKEN")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        raise ListmonkError(f"missing required environment variable(s): {', '.join(missing)}")
    return ListmonkClient(
        base_url=os.environ["LISTMONK_URL"],
        api_user=os.environ["LISTMONK_API_USER"],
        api_token=os.environ["LISTMONK_API_TOKEN"],
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=sorted(TEMPLATES_BY_SLUG), help="Sync only one template")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without calling listmonk")
    parser.add_argument(
        "--set-campaign-default",
        action="store_true",
        help="After syncing the campaign template, set it as listmonk's default template",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    specs = [TEMPLATES_BY_SLUG[args.only]] if args.only else list(TEMPLATES)
    client = None if args.dry_run else _client_from_env()

    for spec in specs:
        result = sync_template(
            client,
            spec,
            dry_run=args.dry_run,
            set_campaign_default=args.set_campaign_default,
        )
        suffix = f" id={result.template_id}" if result.template_id is not None else ""
        print(f"{result.action}: {result.slug} ({result.name}){suffix}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
